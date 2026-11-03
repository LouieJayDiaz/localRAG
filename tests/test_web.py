from pathlib import Path
import threading
from typing import Any

from fastapi.testclient import TestClient

from localrag.chat import LocalChat
from localrag.embeddings import EmbeddingError
from localrag.ingestion import Document
from localrag.search import LocalIndex
from localrag.web import (
    ChatRequest,
    MAX_QUESTION_CHARACTERS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    LocalRAGServer,
    create_app,
)


class UnusedOllama:
    def list_models(self, *, timeout_seconds: float) -> list[Any]:
        return []


def client(tmp_path: Path) -> TestClient:
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())
    return TestClient(create_app(server))


def test_uploads_with_same_name_are_kept_separately(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/documents",
            files=[
                ("uploads", ("notes.md", b"first document", "text/markdown")),
                ("uploads", ("notes.md", b"second document", "text/markdown")),
            ],
        )

        assert response.status_code == 200
        assert response.json()["documents_added"] == 2
        state = browser.get("/api/state").json()
        documents = state["documents"]
        display_names = [item["name"] for item in state["document_items"]]

    assert len(documents) == 2
    assert len(set(documents)) == 2
    assert all(name.startswith("notes--") for name in documents)
    assert display_names == ["notes.md", "notes.md"]


def test_clear_removes_indexed_upload_copies(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        assert browser.post(
            "/api/documents",
            files={"uploads": ("notes.md", b"document", "text/markdown")},
        ).status_code == 200
        assert list((tmp_path / "uploads").iterdir())

        response = browser.post("/api/clear")

        assert response.status_code == 200
        assert browser.get("/api/state").json()["document_count"] == 0

    assert list((tmp_path / "uploads").iterdir()) == []


def test_delete_document_removes_index_entry_and_uploaded_copy(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        uploaded = browser.post(
            "/api/documents",
            files={"uploads": ("notes.md", b"delete this document", "text/markdown")},
        )
        assert uploaded.status_code == 200
        name = browser.get("/api/state").json()["documents"][0]

        deleted = browser.delete(f"/api/documents/{name}")

        assert deleted.status_code == 200
        assert browser.get("/api/state").json()["document_count"] == 0

    assert list((tmp_path / "uploads").iterdir()) == []


def test_delete_document_rejects_unknown_identifier(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        assert browser.delete("/api/documents/missing.md").status_code == 404


def test_stream_chat_emits_sources_tokens_and_completion(
    tmp_path: Path, monkeypatch: Any
) -> None:
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())
    with LocalIndex(server.index_path) as index:
        index.add_documents(
            [Document(tmp_path / "notes.md", "The answer concerns question context.")]
        )
    monkeypatch.setattr(
        LocalChat,
        "stream_tokens",
        lambda *_args, **_kwargs: iter(["First ", "second. [1]"]),
    )

    with TestClient(create_app(server)) as browser:
        response = browser.post(
            "/api/chat/stream",
            json={"question": "question context", "model": "ollama:test"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "sources"' in response.text
    assert '"type": "token", "text": "First "' in response.text
    assert '"type":"complete"' in response.text


def test_health_endpoint_reports_configuration_without_content(
    tmp_path: Path, monkeypatch: Any
) -> None:
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())
    monkeypatch.setattr(server, "state", lambda: {"document_count": 2})
    monkeypatch.setattr(
        server,
        "scan_models",
        lambda: {"providers": [{"name": "Ollama", "id": "ollama", "available": False, "models": []}]},
    )

    with TestClient(create_app(server)) as browser:
        response = browser.get("/api/health")

    body = response.json()
    assert response.status_code == 200
    assert body["backend"] == "sqlite"
    assert body["document_count"] == 2
    assert body["disk_free_bytes"] > 0
    assert "data_directory" in body
    assert "documents" not in body


def test_oversized_upload_is_removed_without_indexing(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/documents",
            files={
                "uploads": (
                    "large.md",
                    b"x" * (MAX_UPLOAD_BYTES + 1),
                    "text/markdown",
                )
            },
        )

        assert response.status_code == 400
        assert browser.get("/api/state").json()["document_count"] == 0

    assert list((tmp_path / "uploads").iterdir()) == []


def test_chat_reports_embedding_provider_failure_as_gateway_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise EmbeddingError("nomic-embed-text is unavailable")

    monkeypatch.setattr(LocalChat, "retrieve", fail)
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/chat",
            json={"question": "What is in my files?", "model": "ollama:test"},
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "nomic-embed-text is unavailable"


def test_chat_releases_index_lock_before_model_generation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    generation_started = threading.Event()
    finish_generation = threading.Event()
    state_completed = threading.Event()
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())
    with LocalIndex(server.index_path) as index:
        index.add_documents(
            [Document(tmp_path / "notes.md", "A matching question context.")]
        )

    def slow_generate(_chat: LocalChat, _prompt: str) -> str:
        generation_started.set()
        assert finish_generation.wait(3)
        return "answer [1]"

    monkeypatch.setattr(LocalChat, "_generate", slow_generate)
    request = ChatRequest(question="question", model="ollama:test")
    chat_thread = threading.Thread(target=lambda: server.chat(request))
    chat_thread.start()
    assert generation_started.wait(2)

    state_thread = threading.Thread(
        target=lambda: (server.state(), state_completed.set())
    )
    state_thread.start()
    try:
        assert state_completed.wait(1)
    finally:
        finish_generation.set()
    chat_thread.join(timeout=3)
    state_thread.join(timeout=3)
    assert not chat_thread.is_alive()
    assert not state_thread.is_alive()


def test_chat_rejects_questions_above_configured_limit(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/chat",
            json={
                "question": "x" * (MAX_QUESTION_CHARACTERS + 1),
                "model": "ollama:test",
            },
        )

    assert response.status_code == 422


def test_upload_rejects_batches_with_too_many_files(tmp_path: Path) -> None:
    files = [
        ("uploads", (f"note-{index}.txt", b"content", "text/plain"))
        for index in range(MAX_UPLOAD_FILES + 1)
    ]
    with client(tmp_path) as browser:
        response = browser.post("/api/documents", files=files)

    assert response.status_code == 413
    assert list((tmp_path / "uploads").iterdir()) == []


def test_chat_rejects_blank_question_as_validation_error(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/chat", json={"question": "   ", "model": "ollama:test"}
        )

    assert response.status_code == 422


def test_chat_limits_follow_up_history(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/chat",
            json={
                "question": "Question",
                "model": "ollama:test",
                "history": [{"role": "user", "content": "earlier"}] * 21,
            },
        )

    assert response.status_code == 422


def test_upload_rejects_aggregate_batch_size(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("localrag.web.MAX_UPLOAD_BATCH_BYTES", 5)
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/documents",
            files={"uploads": ("notes.md", b"sixsix", "text/markdown")},
        )

    assert response.status_code == 413
    assert "0 MB batch limit" in response.json()["detail"]
    assert list((tmp_path / "uploads").iterdir()) == []


def test_upload_reports_embedding_failure_as_service_unavailable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class FailingIndex:
        def __enter__(self) -> "FailingIndex":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def add_documents(self, *_args: object, **_kwargs: object) -> int:
            raise EmbeddingError("embedding service is unavailable")

    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())
    monkeypatch.setattr(server, "_open_index", lambda: FailingIndex())
    with TestClient(create_app(server)) as browser:
        response = browser.post(
            "/api/documents",
            files={"uploads": ("notes.md", b"document text", "text/markdown")},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "embedding service is unavailable"
    assert list((tmp_path / "uploads").iterdir()) == []
