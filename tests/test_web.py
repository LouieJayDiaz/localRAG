from pathlib import Path
import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from localrag.chat import LocalChat
from localrag.embeddings import EmbeddingError
from localrag.ingestion import Document, IngestionError
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


def test_image_upload_is_accepted_and_marked_in_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        "localrag.web.read_document",
        lambda path: Document(path, "[Image]\n[OCR text]\nreceipt total"),
    )

    with client(tmp_path) as browser:
        response = browser.post(
            "/api/documents",
            files={"uploads": ("receipt.webp", b"test image bytes", "image/webp")},
        )

        assert response.status_code == 200
        item = browser.get("/api/state").json()["document_items"][0]

    assert item["name"] == "receipt.webp"
    assert item["kind"] == "image"


def test_reuploading_same_source_replaces_indexed_content(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        first = browser.post(
            "/api/documents",
            files={"uploads": ("folder/notes.md", b"oldalpha unique phrase", "text/markdown")},
        )
        assert first.status_code == 200
        second = browser.post(
            "/api/documents",
            files={"uploads": ("folder/notes.md", b"newbravo unique phrase", "text/markdown")},
        )

        assert second.status_code == 200
        assert browser.get("/api/state").json()["document_count"] == 1

    with LocalIndex(tmp_path / "index.sqlite3") as index:
        assert not index.search("oldalpha", limit=5)
        results = index.search("newbravo", limit=5)
    assert len(results) == 1
    assert "newbravo" in results[0].content


def test_folder_uploads_disambiguate_same_basename_by_parent_folder(
    tmp_path: Path,
) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/documents",
            files=[
                ("uploads", ("policies/notes.md", b"policy document", "text/markdown")),
                ("uploads", ("research/notes.md", b"research document", "text/markdown")),
            ],
        )

        assert response.status_code == 200
        items = browser.get("/api/state").json()["document_items"]

    assert sorted(item["name"] for item in items) == [
        "policies--notes.md",
        "research--notes.md",
    ]


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


def test_health_uses_configured_embedding_model(
    tmp_path: Path, monkeypatch: Any
) -> None:
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())
    monkeypatch.setenv("LOCALRAG_VECTOR_BACKEND", "chroma")
    monkeypatch.setenv("LOCALRAG_EMBEDDING_MODEL", "custom-embed:latest")
    monkeypatch.setattr(server, "state", lambda: {"document_count": 0})
    monkeypatch.setattr(
        server,
        "scan_models",
        lambda: {"providers": [{
            "name": "Ollama", "id": "ollama", "available": True,
            "models": [{"name": "custom-embed:latest"}],
        }]},
    )

    result = server.health()

    assert result["backend"] == "hybrid"
    assert result["embedding_ready"] is True
    assert result["embedding_model"] == "custom-embed:latest"


def test_async_index_job_reports_progress_and_indexes_documents(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/documents/jobs",
            files={"uploads": ("notes.md", b"some useful document text", "text/markdown")},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = browser.get(f"/api/documents/jobs/{job_id}").json()
            if job["status"] in {"complete", "error", "cancelled"}:
                break
            time.sleep(0.01)
        assert job["status"] == "complete"
        assert job["completed"] == job["total"] == 1
        assert job["chunks_added"] > 0
        assert browser.get("/api/state").json()["document_count"] == 1


def test_async_index_job_can_be_cancelled_while_reading(
    tmp_path: Path, monkeypatch: Any
) -> None:
    reading = threading.Event()
    finish_read = threading.Event()
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())

    def slow_read(path: Path) -> Document:
        reading.set()
        assert finish_read.wait(3)
        return Document(path.resolve(), "staged document contents")

    monkeypatch.setattr("localrag.web.read_document", slow_read)
    with TestClient(create_app(server)) as browser:
        response = browser.post(
            "/api/documents/jobs",
            files={"uploads": ("notes.md", b"document", "text/markdown")},
        )
        job_id = response.json()["job_id"]
        assert reading.wait(2)
        browser.delete(f"/api/documents/jobs/{job_id}")
        finish_read.set()
        for _ in range(100):
            job = browser.get(f"/api/documents/jobs/{job_id}").json()
            if job["status"] in {"complete", "error", "cancelled"}:
                break
            time.sleep(0.01)

    assert job["status"] == "cancelled"
    assert list((tmp_path / "uploads").iterdir()) == []


def test_index_job_error_uses_original_filename_instead_of_staging_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())

    def fail_read(path: Path) -> Document:
        raise IngestionError(f"Could not read document: {path}")

    monkeypatch.setattr("localrag.web.read_document", fail_read)
    with TestClient(create_app(server)) as browser:
        response = browser.post(
            "/api/documents/jobs",
            files={"uploads": ("notes.md", b"invalid file", "text/markdown")},
        )
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = browser.get(f"/api/documents/jobs/{job_id}").json()
            if job["status"] in {"complete", "error", "cancelled", "interrupted"}:
                break
            time.sleep(0.01)

    assert job["status"] == "error"
    assert "notes.md" in job["message"]
    assert ".upload-" not in job["message"]
    assert list((tmp_path / "uploads").iterdir()) == []


def test_index_job_status_survives_restart_and_marks_interrupted_work(
    tmp_path: Path,
) -> None:
    job_id = "a" * 32
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())
    server._index_jobs[job_id] = {
        "job_id": job_id,
        "status": "indexing",
        "completed": 0,
        "total": 1,
        "message": "Adding documents to the search index...",
    }
    server._write_index_job_status(job_id, server._index_jobs[job_id])

    restarted_server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())

    assert restarted_server.index_job_status(job_id)["status"] == "interrupted"
    assert "Re-upload" in restarted_server.index_job_status(job_id)["message"]


def test_clear_cancels_queued_index_jobs_before_they_can_repopulate_index(
    tmp_path: Path, monkeypatch: Any
) -> None:
    reading = threading.Event()
    finish_read = threading.Event()
    server = LocalRAGServer(data_directory=tmp_path, ollama=UnusedOllama())

    def slow_read(path: Path) -> Document:
        reading.set()
        assert finish_read.wait(3)
        return Document(path.resolve(), "staged document contents")

    monkeypatch.setattr("localrag.web.read_document", slow_read)
    with TestClient(create_app(server)) as browser:
        response = browser.post(
            "/api/documents/jobs",
            files={"uploads": ("notes.md", b"document", "text/markdown")},
        )
        job_id = response.json()["job_id"]
        assert reading.wait(2)
        assert browser.post("/api/clear").status_code == 200
        finish_read.set()
        for _ in range(100):
            job = browser.get(f"/api/documents/jobs/{job_id}").json()
            if job["status"] in {"complete", "error", "cancelled", "interrupted"}:
                break
            time.sleep(0.01)

        assert browser.get("/api/state").json()["document_count"] == 0

    assert job["status"] == "cancelled"
    assert list((tmp_path / "uploads").iterdir()) == []


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


def test_chat_abstains_when_query_has_no_matching_evidence(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        response = browser.post(
            "/api/chat",
            json={"question": "What is the weather on Mars?", "model": "ollama:unused"},
        )

    assert response.status_code == 200
    assert response.json()["sources"] == []
    assert response.json()["answer"].startswith("I couldn't find relevant passages")


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
