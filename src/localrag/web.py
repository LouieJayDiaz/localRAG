"""LocalRAG web application."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Annotated, Literal
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .chat import LocalChat, ModelError
from .chroma_index import ChromaIndex
from .embeddings import EmbeddingError
from .ingestion import IngestionError, read_document
from .ollama import OllamaClient, OllamaError
from .paths import default_data_directory as _default_data_directory
from .search import LocalIndex

SUPPORTED_SUFFIXES = frozenset({".docx", ".md", ".pdf", ".txt"})
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_BATCH_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_FILES = 50
MAX_QUESTION_CHARACTERS = 8_000
UPLOAD_COPY_CHUNK_BYTES = 1024 * 1024
WEB_ROOT = Path(__file__).with_name("web")
_UPLOAD_ID_SUFFIX = re.compile(r"--?[0-9a-f]{12}$")


def _display_document_name(stored_name: str) -> str:
    path = Path(stored_name)
    display_stem = _UPLOAD_ID_SUFFIX.sub("", path.stem)
    return f"{display_stem}{path.suffix}"


def default_data_directory() -> Path:
    """Return the directory used for the persistent local website data."""
    return _default_data_directory()


def default_data_path() -> Path:
    """Return the persistent local index path."""
    return default_data_directory() / "index.sqlite3"


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2_000)

    @field_validator("content")
    @classmethod
    def content_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("chat history entries cannot be blank")
        return value


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS)
    model: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def question_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question cannot be empty")
        return value


class ModelPullRequest(BaseModel):
    name: str = Field(min_length=1)


class LocalRAGServer:
    """Coordinate web requests with the local index and Ollama."""

    def __init__(
        self,
        *,
        data_directory: Path | None = None,
        ollama: OllamaClient | None = None,
    ) -> None:
        self.data_directory = (data_directory or default_data_directory()).expanduser()
        self.data_directory.mkdir(parents=True, exist_ok=True)
        self.upload_directory = self.data_directory / "uploads"
        self.upload_directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.data_directory / "index.sqlite3"
        self.chroma_path = self.data_directory / "chroma"
        self.ollama = ollama or OllamaClient()
        self._index_lock = threading.Lock()
        self._pulls: dict[str, dict[str, object]] = {}
        self._pull_events: dict[str, threading.Event] = {}
        self._pull_lock = threading.Lock()

    def _open_index(self) -> LocalIndex | ChromaIndex:
        if os.environ.get("LOCALRAG_VECTOR_BACKEND", "sqlite").casefold() == "chroma":
            return ChromaIndex(self.chroma_path)
        return LocalIndex(self.index_path)

    def state(self) -> dict[str, object]:
        try:
            with self._open_index() as index:
                paths = index.list_documents()
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return {
            "documents": [path.name for path in paths],
            "document_items": [
                {"id": path.name, "name": _display_document_name(path.name)}
                for path in paths
            ],
            "document_count": len(paths),
        }

    def index_uploads(self, uploads: list[UploadFile]) -> dict[str, object]:
        if not uploads:
            raise HTTPException(status_code=400, detail="Choose at least one document.")
        stored_paths: list[Path] = []
        try:
            if len(uploads) > MAX_UPLOAD_FILES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Choose no more than {MAX_UPLOAD_FILES} files at a time.",
                )
            known_batch_size = sum(upload.size or 0 for upload in uploads)
            if known_batch_size > MAX_UPLOAD_BATCH_BYTES:
                batch_limit_mb = MAX_UPLOAD_BATCH_BYTES // (1024 * 1024)
                raise HTTPException(
                    status_code=413,
                    detail=f"The selected files exceed the {batch_limit_mb} MB batch limit.",
                )
            with self._index_lock:
                used_destinations: set[Path] = set()
                for upload in uploads:
                    filename = Path(upload.filename or "").name
                    suffix = Path(filename).suffix.lower()
                    if not filename or suffix not in SUPPORTED_SUFFIXES:
                        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
                        raise IngestionError(
                            f"Unsupported document type: {filename or '(none)'}; use {supported}"
                        )
                    stem = Path(filename).stem
                    destination = self.upload_directory / f"{stem}--{uuid.uuid4().hex[:12]}{suffix}"
                    stored_paths.append(destination)
                    used_destinations.add(destination)
                    self._copy_upload(upload, destination, filename)

                documents = [read_document(path) for path in stored_paths]
                with self._open_index() as index:
                    chunks = index.add_documents(documents)
        except HTTPException:
            raise
        except (EmbeddingError, RuntimeError) as error:
            for path in stored_paths:
                path.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail=str(error)) from error
        except (IngestionError, OSError, ValueError) as error:
            for path in stored_paths:
                path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(error)) from error
        finally:
            for upload in uploads:
                upload.file.close()
        return {"documents_added": len(stored_paths), "chunks_added": chunks}

    @staticmethod
    def _copy_upload(upload: UploadFile, destination: Path, filename: str) -> None:
        copied = 0
        try:
            with destination.open("wb") as target:
                while True:
                    block = upload.file.read(UPLOAD_COPY_CHUNK_BYTES)
                    if not block:
                        break
                    copied += len(block)
                    if copied > MAX_UPLOAD_BYTES:
                        raise IngestionError(
                            f"{filename} is too large; maximum upload size is "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                        )
                    target.write(block)
        except IngestionError:
            destination.unlink(missing_ok=True)
            raise

    def delete_document(self, document_name: str) -> dict[str, str]:
        """Remove one indexed document and any LocalRAG-managed upload copy."""
        if not document_name or Path(document_name).name != document_name:
            raise HTTPException(status_code=400, detail="Invalid document identifier.")
        try:
            with self._index_lock:
                with self._open_index() as index:
                    matches = [
                        path for path in index.list_documents()
                        if path.name == document_name
                    ]
                    if not matches:
                        raise HTTPException(status_code=404, detail="Document not found.")
                    if len(matches) > 1:
                        raise HTTPException(
                            status_code=409,
                            detail="More than one document has this name; clear and re-add the files.",
                        )
                    path = matches[0].resolve()
                    if not index.remove_document(path):
                        raise HTTPException(status_code=404, detail="Document not found.")
                upload_directory = self.upload_directory.resolve()
                if path.parent == upload_directory:
                    path.unlink(missing_ok=True)
        except HTTPException:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return {"status": "deleted", "document": document_name}

    def chat(self, request: ChatRequest) -> dict[str, object]:
        provider, model = self._split_model(request.model)
        try:
            with self._index_lock:
                with self._open_index() as index:
                    if provider == "lmstudio":
                        chat = LocalChat(
                            index,
                            model=model,
                            endpoint="http://127.0.0.1:1234/v1/chat/completions",
                            protocol="openai",
                        )
                    else:
                        chat = LocalChat(index, model=model)
                    sources = chat.retrieve(
                        request.question,
                        limit=request.limit,
                        threshold=request.threshold,
                        history=[(turn.role, turn.content) for turn in request.history],
                    )
            response = chat.answer_with_sources(
                request.question,
                sources,
                history=[(turn.role, turn.content) for turn in request.history],
            )
        except (EmbeddingError, ModelError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return {
            "answer": response.answer,
            "sources": [
                {
                    "document": _display_document_name(source.document_path.name),
                    "chunk": source.chunk_index,
                    "score": source.score,
                    "content": source.content,
                }
                for source in response.sources
            ],
        }

    @staticmethod
    def _split_model(value: str) -> tuple[str, str]:
        provider, separator, model = value.partition(":")
        if not separator or provider not in {"ollama", "lmstudio"} or not model.strip():
            raise HTTPException(
                status_code=400,
                detail="Select a model from a detected local provider.",
            )
        return provider, model

    def scan_models(self) -> dict[str, object]:
        providers: list[dict[str, object]] = []
        try:
            models = self.ollama.list_models(timeout_seconds=2.0)
        except OllamaError as error:
            providers.append({
                "name": "Ollama",
                "id": "ollama",
                "available": False,
                "library": "https://ollama.com/library",
                "error": str(error),
                "models": [],
            })
        else:
            providers.append({
                "name": "Ollama",
                "id": "ollama",
                "available": True,
                "library": "https://ollama.com/library",
                "models": [
                    {"id": f"ollama:{model.name}", "name": model.name, "size": model.size_label}
                    for model in models
                ],
            })

        try:
            request = Request("http://127.0.0.1:1234/v1/models")
            with urlopen(request, timeout=2) as response:
                body = json.loads(response.read().decode("utf-8"))
            raw_models = body.get("data", [])
            if not isinstance(raw_models, list):
                raise ValueError("invalid model list")
            providers.append({
                "name": "LM Studio",
                "id": "lmstudio",
                "available": True,
                "library": "https://lmstudio.ai/models",
                "models": [
                    {"id": f"lmstudio:{item['id']}", "name": item["id"], "size": ""}
                    for item in raw_models
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ],
            })
        except (
            OSError,
            URLError,
            ValueError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            providers.append({
                "name": "LM Studio",
                "id": "lmstudio",
                "available": False,
                "library": "https://lmstudio.ai/models",
                "error": f"LM Studio is not running on port 1234: {error}",
                "models": [],
            })
        return {"providers": providers}

    def health(self) -> dict[str, object]:
        """Return local configuration diagnostics without document contents."""
        backend = os.environ.get("LOCALRAG_VECTOR_BACKEND", "sqlite").casefold()
        index_error = None
        try:
            document_count = self.state()["document_count"]
        except HTTPException as error:
            document_count = None
            index_error = str(error.detail)
        providers = self.scan_models()["providers"]
        ollama = next(
            (provider for provider in providers if provider["id"] == "ollama"),
            None,
        )
        embedding_ready = None
        if backend == "chroma":
            embedding_ready = bool(
                ollama
                and ollama["available"]
                and any(
                    model["name"].split(":", 1)[0] == "nomic-embed-text"
                    for model in ollama["models"]
                )
            )
        try:
            free_bytes = shutil.disk_usage(self.data_directory).free
        except OSError:
            free_bytes = None
        return {
            "backend": backend,
            "data_directory": str(self.data_directory),
            "document_count": document_count,
            "index_error": index_error,
            "disk_free_bytes": free_bytes,
            "embedding_ready": embedding_ready,
            "providers": providers,
        }

    def pull_model(self, request: ModelPullRequest) -> dict[str, str]:
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Model name cannot be empty.")
        job_id = uuid.uuid4().hex
        with self._pull_lock:
            self._pulls[job_id] = {"status": "starting", "model": name}
            self._pull_events[job_id] = threading.Event()

        def download() -> None:
            try:
                self._set_pull(job_id, status="downloading", message=f"Downloading {name}...")
                self.ollama.pull_model(
                    name,
                    stop_event=self._pull_events[job_id],
                    progress=lambda event: self._set_pull(
                        job_id,
                        message=str(event.get("status", "Downloading...")),
                        completed=event.get("completed", 0),
                        total=event.get("total", 0),
                    ),
                )
            except (OllamaError, ValueError) as error:
                if self._pull_events[job_id].is_set():
                    self._set_pull(job_id, status="cancelled", message="Download cancelled.")
                else:
                    self._set_pull(job_id, status="error", message=str(error))
            else:
                self._set_pull(job_id, status="complete", message=f"{name} is ready.")

        threading.Thread(target=download, name=f"localrag-pull-{job_id}", daemon=True).start()
        return {"job_id": job_id, "model": name}

    def cancel_pull(self, job_id: str) -> dict[str, object]:
        with self._pull_lock:
            event = self._pull_events.get(job_id)
            status = self._pulls.get(job_id)
        if event is None or status is None:
            raise HTTPException(status_code=404, detail="Model download was not found.")
        if status["status"] in {"complete", "error", "cancelled"}:
            return dict(status)
        event.set()
        self._set_pull(job_id, status="cancelling", message="Cancelling download...")
        return self.pull_status(job_id)

    def pull_status(self, job_id: str) -> dict[str, object]:
        with self._pull_lock:
            status = self._pulls.get(job_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Model download was not found.")
        return dict(status)

    def _set_pull(self, job_id: str, **values: object) -> None:
        with self._pull_lock:
            if job_id in self._pulls:
                self._pulls[job_id].update(values)

    def clear(self) -> dict[str, str]:
        try:
            with self._index_lock:
                with self._open_index() as index:
                    index.clear()
                for path in self.upload_directory.iterdir():
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return {"status": "cleared"}


def create_app(server: LocalRAGServer | None = None) -> FastAPI:
    """Create the LocalRAG web application."""
    state = server or LocalRAGServer()
    app = FastAPI(title="LocalRAG", version="0.1.0")

    @app.get("/api/state")
    def get_state() -> dict[str, object]:
        return state.state()

    @app.get("/api/models")
    def get_models() -> dict[str, object]:
        return state.scan_models()

    @app.get("/api/health")
    def get_health() -> dict[str, object]:
        return state.health()

    @app.post("/api/documents")
    def upload_documents(
        uploads: Annotated[list[UploadFile], File(description="Documents to index")],
    ) -> dict[str, object]:
        return state.index_uploads(uploads)

    @app.delete("/api/documents/{document_name}")
    def delete_document(document_name: str) -> dict[str, str]:
        return state.delete_document(document_name)

    @app.post("/api/chat")
    def ask_chat(request: ChatRequest) -> dict[str, object]:
        return state.chat(request)

    @app.post("/api/chat/stream")
    def ask_chat_stream(request: ChatRequest) -> StreamingResponse:
        provider, model = state._split_model(request.model)
        try:
            with state._index_lock:
                with state._open_index() as index:
                    if provider == "lmstudio":
                        chat = LocalChat(
                            index,
                            model=model,
                            endpoint="http://127.0.0.1:1234/v1/chat/completions",
                            protocol="openai",
                        )
                    else:
                        chat = LocalChat(index, model=model)
                    sources = chat.retrieve(
                        request.question,
                        limit=request.limit,
                        threshold=request.threshold,
                        history=[(turn.role, turn.content) for turn in request.history],
                    )
        except (EmbeddingError, ModelError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

        def events():
            source_data = [
                {
                    "document": _display_document_name(source.document_path.name),
                    "chunk": source.chunk_index,
                    "score": source.score,
                    "content": source.content,
                }
                for source in sources
            ]
            yield f"data: {json.dumps({'type': 'sources', 'sources': source_data})}\n\n"
            try:
                for token in chat.stream_tokens(
                    request.question,
                    sources,
                    history=[(turn.role, turn.content) for turn in request.history],
                ):
                    yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
            except (EmbeddingError, ModelError, OSError, RuntimeError, ValueError) as error:
                yield f"data: {json.dumps({'type': 'error', 'message': str(error)})}\n\n"
                return
            yield "data: {\"type\":\"complete\"}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/models/pull")
    def pull_model(request: ModelPullRequest) -> dict[str, str]:
        return state.pull_model(request)

    @app.get("/api/models/pull/{job_id}")
    def pull_status(job_id: str) -> dict[str, object]:
        return state.pull_status(job_id)

    @app.delete("/api/models/pull/{job_id}")
    def cancel_model_pull(job_id: str) -> dict[str, object]:
        return state.cancel_pull(job_id)

    @app.post("/api/clear")
    def clear_index() -> dict[str, str]:
        return state.clear()

    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    return app


app = create_app()


def main() -> int:
    """Run the LocalRAG website."""
    parser = argparse.ArgumentParser(description="Run the LocalRAG website")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="allow access from other devices (the API has no authentication)",
    )
    arguments = parser.parse_args()
    try:
        loopback = ipaddress.ip_address(arguments.host).is_loopback
    except ValueError:
        loopback = arguments.host.casefold() == "localhost"
    if not loopback and not arguments.allow_network:
        parser.error("binding beyond localhost requires the explicit --allow-network flag")

    import uvicorn

    uvicorn.run(app, host=arguments.host, port=arguments.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
