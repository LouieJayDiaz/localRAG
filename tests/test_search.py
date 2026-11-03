from pathlib import Path
import sqlite3

import pytest

from localrag.ingestion import Document
from localrag.search import LocalIndex


def document(content: str, name: str) -> Document:
    return Document(Path(name).resolve(), content)


def test_index_searches_relevant_content_and_persists(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite3"
    documents = [
        document("Python uses indentation to define code blocks.", "python.md"),
        document("Windows applications can use native installers.", "windows.md"),
    ]

    with LocalIndex(database) as index:
        assert index.add_documents(documents, max_characters=200) == 2
        results = index.search("How does Python define code blocks?", limit=1)

    assert results[0].document_path.name == "python.md"
    assert results[0].score > 0

    with LocalIndex(database) as reopened:
        assert reopened.search("native Windows installer", limit=1)[0].document_path.name == "windows.md"


def test_reindexing_replaces_old_chunks(tmp_path: Path) -> None:
    source = document("old content", "notes.md")
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents([source])
        count = index.add_documents([document("new content", "notes.md")])
        results = index.search("new content")

    assert count == 1
    assert results[0].content == "new content"
    with LocalIndex(tmp_path / "index.sqlite3") as reopened:
        assert reopened.search("old") == []


def test_existing_sqlite_index_is_backfilled_into_fts(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE chunks (document_path TEXT NOT NULL, chunk_index INTEGER NOT NULL, "
        "content TEXT NOT NULL, embedding TEXT NOT NULL, PRIMARY KEY(document_path, chunk_index))"
    )
    connection.execute(
        "INSERT INTO chunks VALUES (?, ?, ?, ?)",
        (str((tmp_path / "legacy.md").resolve()), 0, "Legacy search content", "[]"),
    )
    connection.commit()
    connection.close()

    with LocalIndex(database) as index:
        results = index.search("legacy content")

    assert len(results) == 1
    assert results[0].document_path.name == "legacy.md"


def test_index_lists_and_clears_documents(tmp_path: Path) -> None:
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents(
            [document("one", "b.md"), document("two", "a.md")]
        )
        assert [path.name for path in index.list_documents()] == ["a.md", "b.md"]

        index.clear()

        assert index.list_documents() == []
        assert index.search("one") == []


def test_index_removes_one_document(tmp_path: Path) -> None:
    first = document("alpha document", str(tmp_path / "alpha.md"))
    second = document("beta document", str(tmp_path / "beta.md"))
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents([first, second])

        assert index.remove_document(first.path)
        assert not index.remove_document(first.path)
        assert [path.name for path in index.list_documents()] == ["beta.md"]
        assert index.search("alpha") == []


def test_search_does_not_return_unrelated_chunks(tmp_path: Path) -> None:
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents(
            [document("apples and oranges", "fruit.md")]
        )

        assert index.search("what is in this project?") == []


def test_search_weights_distinctive_terms_more_than_common_terms(tmp_path: Path) -> None:
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents(
            [
                document("Python is a popular language.", "common.md"),
                document("Python and pandas analyze tabular data.", "specific.md"),
                document("Python is used for scripting.", "another.md"),
            ]
        )

        results = index.search("Python pandas")

    assert results[0].document_path.name == "specific.md"


def test_search_matches_common_word_inflections(tmp_path: Path) -> None:
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents(
            [document("Employee requests are reviewed by managers.", "requests.md")]
        )

        results = index.search("employee request review")

    assert results[0].document_path.name == "requests.md"


def test_search_threshold_filters_low_scoring_chunks(tmp_path: Path) -> None:
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents(
            [document("Python defines code blocks with indentation.", "python.md")]
        )

        assert index.search("Python", threshold=0.9) == []
        assert index.search("Python defines code blocks", threshold=0.2)


@pytest.mark.parametrize(
    ("query", "limit"),
    [("", 5), ("query", 0), ("query", -1)],
)
def test_search_rejects_invalid_arguments(
    tmp_path: Path, query: str, limit: int
) -> None:
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        with pytest.raises(ValueError):
            index.search(query, limit=limit)
