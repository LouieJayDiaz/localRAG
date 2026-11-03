"""Combine SQLite lexical search with an optional Chroma semantic index."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .chroma_index import ChromaIndex
from .embeddings import EmbeddingError
from .ingestion import Document
from .search import LocalIndex, SearchResult


class HybridIndex:
    """Use reciprocal-rank fusion for lexical and semantic results.

    SQLite remains the durable source of truth. If the embedding service is
    unavailable, writes and searches continue through SQLite without returning
    stale semantic chunks for documents that were just replaced.
    """

    _RRF_K = 60

    def __init__(self, sqlite_path: Path, chroma_path: Path) -> None:
        self.lexical = LocalIndex(sqlite_path)
        try:
            self.semantic = ChromaIndex(chroma_path)
        except Exception:
            self.lexical.close()
            raise

    def __enter__(self) -> "HybridIndex":
        return self

    def __exit__(self, *_: object) -> None:
        try:
            self.semantic.__exit__()
        finally:
            self.lexical.close()

    def list_documents(self) -> list[Path]:
        paths = {str(path.resolve()) for path in self.lexical.list_documents()}
        paths.update(str(path.resolve()) for path in self.semantic.list_documents())
        return sorted((Path(path) for path in paths), key=str)

    def clear(self) -> None:
        self.lexical.clear()
        self.semantic.clear()

    def remove_document(self, document_path: Path) -> bool:
        lexical_removed = self.lexical.remove_document(document_path)
        semantic_removed = self.semantic.remove_document(document_path)
        return lexical_removed or semantic_removed

    def add_documents(
        self,
        documents: Iterable[Document],
        *,
        max_characters: int = 1_000,
        overlap_characters: int | None = None,
    ) -> int:
        document_list = list(documents)
        chunks = self.lexical.add_documents(
            document_list,
            max_characters=max_characters,
            overlap_characters=overlap_characters,
        )
        try:
            self.semantic.add_documents(
                document_list,
                max_characters=max_characters,
                overlap_characters=overlap_characters,
            )
        except EmbeddingError:
            for document in document_list:
                self.semantic.remove_document(document.path)
        return chunks

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        threshold: float = 0.0,
        semantic_fallback: bool = False,
    ) -> list[SearchResult]:
        del semantic_fallback
        candidate_limit = max(limit * 4, 20)
        lexical = self.lexical.search(query, limit=candidate_limit)
        try:
            semantic = self.semantic.search(query, limit=candidate_limit)
        except EmbeddingError:
            return [result for result in lexical if result.score >= threshold][:limit]

        rankings: dict[tuple[str, int], tuple[SearchResult, float]] = {}
        for results in (lexical, semantic):
            for rank, result in enumerate(results, start=1):
                key = (str(result.document_path.resolve()), result.chunk_index)
                contribution = 1.0 / (self._RRF_K + rank)
                previous = rankings.get(key)
                if previous is None:
                    rankings[key] = (result, contribution)
                else:
                    rankings[key] = (previous[0], previous[1] + contribution)

        maximum_score = 2.0 / (self._RRF_K + 1)
        combined = [
            SearchResult(
                document_path=result.document_path,
                chunk_index=result.chunk_index,
                content=result.content,
                score=score / maximum_score,
            )
            for result, score in rankings.values()
            if score / maximum_score >= threshold
        ]
        combined.sort(key=lambda result: (-result.score, str(result.document_path), result.chunk_index))
        return combined[:limit]
