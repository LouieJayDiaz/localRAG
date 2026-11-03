from pathlib import Path

import pytest

from localrag.chroma_index import ChromaIndex
from localrag.ingestion import Document


class Collection:
    def query(self, **_: object) -> dict[str, list[list[object]]]:
        return {
            "documents": [["close", "far"]],
            "metadatas": [[
                {"document_path": "close.md", "chunk_index": 0},
                {"document_path": "far.md", "chunk_index": 1},
            ]],
            "distances": [[0.0, 9.0]],
        }


def test_chroma_search_applies_score_threshold() -> None:
    index = object.__new__(ChromaIndex)
    index.collection = Collection()

    results = index.search("query", threshold=0.5)

    assert [result.document_path for result in results] == [Path("close.md")]


def test_chroma_search_rejects_empty_query() -> None:
    index = object.__new__(ChromaIndex)
    index.collection = Collection()

    with pytest.raises(ValueError, match="query cannot be empty"):
        index.search("   ")


def test_chroma_search_reports_incomplete_result_arrays() -> None:
    class IncompleteCollection:
        def query(self, **_: object) -> dict[str, list[list[object]]]:
            return {"documents": [["text"]], "metadatas": [[]], "distances": [[]]}

    index = object.__new__(ChromaIndex)
    index.collection = IncompleteCollection()

    with pytest.raises(RuntimeError, match="incomplete search results"):
        index.search("query")


def test_chroma_reindex_keeps_old_chunks_when_upsert_fails() -> None:
    class FailingCollection:
        def get(self, **_: object) -> dict[str, list[str]]:
            return {"ids": ["notes.md::0"]}

        def upsert(self, **_: object) -> None:
            raise RuntimeError("embedding failed")

        def delete(self, **_: object) -> None:
            raise AssertionError("old chunks should not be deleted")

    index = object.__new__(ChromaIndex)
    index.collection = FailingCollection()

    try:
        index.add_documents([Document(Path("notes.md"), "replacement")])
    except RuntimeError as error:
        assert str(error) == "embedding failed"
    else:
        raise AssertionError("expected the replacement to fail")
