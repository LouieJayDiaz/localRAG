from pathlib import Path
from typing import Any

from localrag.embeddings import EmbeddingError
from localrag.hybrid_index import HybridIndex
from localrag.ingestion import Document
from localrag.search import SearchResult


def result(path: Path, chunk: int, score: float = 0.5) -> SearchResult:
    return SearchResult(path, chunk, f"text from {path.name}", score)


class FakeIndex:
    def __init__(self, results: list[SearchResult] | None = None, error: Exception | None = None) -> None:
        self.results = results or []
        self.error = error
        self.removed: list[Path] = []

    def search(self, *_args: Any, **_kwargs: Any) -> list[SearchResult]:
        if self.error:
            raise self.error
        return self.results

    def add_documents(self, *_args: Any, **_kwargs: Any) -> int:
        if self.error:
            raise self.error
        return 1

    def remove_document(self, path: Path) -> bool:
        self.removed.append(path)
        return True


def test_hybrid_search_fuses_lexical_and_semantic_rankings() -> None:
    path_a = Path("a.md")
    path_b = Path("b.md")
    index = object.__new__(HybridIndex)
    index.lexical = FakeIndex([result(path_a, 0), result(path_b, 0)])
    index.semantic = FakeIndex([result(path_b, 0), result(path_a, 0)])

    results = index.search("query", limit=2)

    assert {item.document_path for item in results} == {path_a, path_b}
    assert results[0].score == results[1].score
    assert all(0.99 < item.score < 1 for item in results)


def test_hybrid_search_falls_back_to_lexical_when_embeddings_are_unavailable() -> None:
    lexical = [result(Path("notes.md"), 0, score=0.42)]
    index = object.__new__(HybridIndex)
    index.lexical = FakeIndex(lexical)
    index.semantic = FakeIndex(error=EmbeddingError("embedding service offline"))

    assert index.search("query", limit=3, threshold=0.2) == lexical


def test_hybrid_reindex_removes_stale_semantic_chunks_on_embedding_failure() -> None:
    document = Document(Path("notes.md"), "updated content")
    lexical = FakeIndex()
    semantic = FakeIndex(error=EmbeddingError("embedding service offline"))
    index = object.__new__(HybridIndex)
    index.lexical = lexical
    index.semantic = semantic

    assert index.add_documents([document]) == 1
    assert semantic.removed == [document.path]
