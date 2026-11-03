import json
from pathlib import Path

import pytest

from localrag.embeddings import EmbeddingError
from localrag.hybrid_index import HybridIndex
from localrag.ingestion import Document


class EmbeddingResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "EmbeddingResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_chroma_hybrid_index_runs_with_real_local_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("chromadb")
    monkeypatch.setattr(
        "localrag.embeddings.urlopen",
        lambda request, **_kwargs: EmbeddingResponse({
            "embeddings": [
                [1.0, 0.0, 0.0]
                for _ in json.loads(request.data)["input"]
            ]
        }),
    )
    source = tmp_path / "policy.md"

    with HybridIndex(tmp_path / "index.sqlite3", tmp_path / "chroma") as index:
        assert index.add_documents(
            [Document(source, "Paid time away from work is available to employees.")]
        ) == 1
        results = index.search("vacation policy", limit=3)

    with HybridIndex(tmp_path / "index.sqlite3", tmp_path / "chroma") as reopened:
        reopened_results = reopened.search("vacation policy", limit=3)

    assert [result.document_path for result in results] == [source]
    assert [result.document_path for result in reopened_results] == [source]


def test_chroma_hybrid_index_keeps_lexical_service_when_ollama_is_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("chromadb")

    def offline(*_args: object, **_kwargs: object) -> None:
        raise EmbeddingError("offline")

    monkeypatch.setattr("localrag.embeddings.urlopen", offline)
    source = tmp_path / "policy.md"
    with HybridIndex(tmp_path / "index.sqlite3", tmp_path / "chroma") as index:
        assert index.add_documents(
            [Document(source, "Annual leave provides fifteen paid vacation days.")]
        ) == 1
        results = index.search("vacation days", limit=3)

    assert [result.document_path for result in results] == [source]
