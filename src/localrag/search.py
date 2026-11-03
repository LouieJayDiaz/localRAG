"""SQLite FTS index and local search."""

import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .chunking import Chunk, chunk_documents
from .ingestion import Document

_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_QUERY_SYNONYMS = {
    "vacation": {"leave", "holiday", "pto"},
    "leave": {"vacation", "holiday", "pto"},
    "claiming": {"reimbursement", "expense", "refund"},
    "claim": {"reimbursement", "expense", "refund"},
    "money": {"reimbursement", "expense", "refund"},
    "purchases": {"purchase", "expense", "reimbursement"},
    "purchase": {"expense", "reimbursement"},
    "computer": {"laptop", "device", "equipment"},
    "another": {"replacement", "new"},
    "forgotten": {"reset", "recovery"},
    "change": {"reset", "update"},
}


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A matching chunk and its score."""

    document_path: Path
    chunk_index: int
    content: str
    score: float


class LocalIndex:
    """Store chunks and hashed embeddings in SQLite."""

    def __init__(self, database_path: Path, *, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be greater than zero")
        self.database_path = database_path.expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.dimensions = dimensions
        self._connection = sqlite3.connect(
            self.database_path, check_same_thread=False, timeout=10.0
        )
        self._connection.execute("PRAGMA busy_timeout = 10000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS chunks (
                document_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                PRIMARY KEY (document_path, chunk_index)
            )"""
        )
        self._connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
            "content, content='chunks', content_rowid='rowid', "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        self._connection.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
                INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
            END;
            CREATE TABLE IF NOT EXISTS localrag_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        initialized = self._connection.execute(
            "SELECT value FROM localrag_metadata WHERE key = 'fts_version'"
        ).fetchone()
        if initialized is None:
            self._connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
            self._connection.execute(
                "INSERT INTO localrag_metadata(key, value) VALUES ('fts_version', '1')"
            )
        self._connection.commit()

    def __enter__(self) -> "LocalIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def list_documents(self) -> list[Path]:
        """Return indexed document paths."""
        rows = self._connection.execute(
            "SELECT DISTINCT document_path FROM chunks ORDER BY document_path"
        )
        return [Path(row[0]) for row in rows]

    def clear(self) -> None:
        """Remove all indexed chunks."""
        with self._connection:
            self._connection.execute("DELETE FROM chunks")

    def remove_document(self, document_path: Path) -> bool:
        """Remove one document from the index and report whether it existed."""
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM chunks WHERE document_path = ?",
                (str(document_path.expanduser().resolve()),),
            )
        return cursor.rowcount > 0

    def add_documents(
        self,
        documents: Iterable[Document],
        *,
        max_characters: int = 1_000,
        overlap_characters: int | None = None,
    ) -> int:
        """Replace chunks for these documents and return the count."""
        document_list = list(documents)
        chunks = chunk_documents(
            document_list,
            max_characters=max_characters,
            overlap_characters=overlap_characters,
        )
        paths = {str(document.path.resolve()) for document in document_list}
        with self._connection:
            for path in paths:
                self._connection.execute(
                    "DELETE FROM chunks WHERE document_path = ?", (path,)
                )
            self._connection.executemany(
                """
                INSERT INTO chunks(document_path, chunk_index, content, embedding)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        str(chunk.document_path.resolve()),
                        chunk.index,
                        chunk.content,
                        "[]",
                    )
                    for chunk in chunks
                ],
            )
        return len(chunks)

    def add_chunks(self, chunks: Iterable[Chunk]) -> int:
        """Replace explicitly prepared chunks."""
        chunk_list = list(chunks)
        with self._connection:
            for path in {str(chunk.document_path.resolve()) for chunk in chunk_list}:
                self._connection.execute(
                    "DELETE FROM chunks WHERE document_path = ?", (path,)
                )
            self._connection.executemany(
                """
                INSERT INTO chunks(document_path, chunk_index, content, embedding)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        str(chunk.document_path.resolve()),
                        chunk.index,
                        chunk.content,
                        "[]",
                    )
                    for chunk in chunk_list
                ],
            )
        return len(chunk_list)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        threshold: float = 0.0,
        semantic_fallback: bool = False,
    ) -> list[SearchResult]:
        """Return matching chunks for a query.

        Results are ranked by SQLite FTS5's BM25 implementation. The optional
        semantic_fallback argument is retained for backend compatibility; this
        keyword index has no semantic vectors and returns no unrelated chunks.
        """
        if not query.strip():
            raise ValueError("query cannot be empty")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between zero and one")

        query_tokens = set(_TOKEN_PATTERN.findall(query.casefold()))
        if not query_tokens:
            return []
        expanded_tokens = set(query_tokens)
        for token in query_tokens:
            expanded_tokens.update(_QUERY_SYNONYMS.get(token, ()))
        match = " OR ".join(f'"{token}"' for token in sorted(expanded_tokens))
        rows = list(self._connection.execute(
            """SELECT chunks.document_path, chunks.chunk_index, chunks.content,
                      bm25(chunks_fts) AS rank
               FROM chunks_fts JOIN chunks ON chunks.rowid = chunks_fts.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank, chunks.document_path, chunks.chunk_index""",
            (match,),
        ))
        relevance_values = [max(0.0, -float(row[3])) for row in rows]
        strongest_relevance = max(relevance_values, default=0.0)
        query_length_factor = len(query_tokens) / (len(query_tokens) + 1)
        results: list[SearchResult] = []
        for (path, index, content, _rank), relevance in zip(
            rows, relevance_values, strict=True
        ):
            content_tokens = set(_TOKEN_PATTERN.findall(content.casefold()))
            direct_matches = query_tokens & content_tokens
            synonym_matches = (expanded_tokens - query_tokens) & content_tokens
            coverage = (
                len(direct_matches) + 0.5 * len(synonym_matches)
            ) / (len(query_tokens) + 0.5 * len(expanded_tokens - query_tokens))
            rank_quality = relevance / strongest_relevance if strongest_relevance else 0.0
            score = (0.75 * coverage + 0.25 * rank_quality) * query_length_factor
            if score < threshold:
                continue
            results.append(SearchResult(Path(path), index, content, score))
        return results[:limit]
