"""Small deterministic retrieval benchmark for the built-in SQLite backend."""

from pathlib import Path

from localrag.ingestion import Document
from localrag.search import LocalIndex


def test_default_search_recall_at_three(tmp_path: Path) -> None:
    corpus = {
        "leave.md": "Annual leave policy: employees receive fifteen paid vacation days each year.",
        "expenses.md": "Expense reimbursement requires a receipt and manager approval.",
        "security.md": "Reset your password from account settings using the recovery email.",
        "hardware.md": "Request a replacement laptop through the IT equipment form.",
    }
    cases = [
        ("how many paid vacation days are offered", "leave.md"),
        ("how do I request annual leave", "leave.md"),
        ("claiming money back for work purchases", "expenses.md"),
        ("where to change a forgotten password", "security.md"),
        ("get another work computer", "hardware.md"),
    ]

    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents(
            [Document(tmp_path / name, text) for name, text in corpus.items()],
            max_characters=300,
            overlap_characters=0,
        )
        hits = sum(
            expected in {result.document_path.name for result in index.search(query, limit=3)}
            for query, expected in cases
        )

    assert hits / len(cases) >= 0.8


def test_default_search_ignores_question_filler_for_unrelated_queries(
    tmp_path: Path,
) -> None:
    with LocalIndex(tmp_path / "index.sqlite3") as index:
        index.add_documents(
            [
                Document(tmp_path / "leave.md", "Annual leave policy and paid vacation days."),
                Document(tmp_path / "security.md", "Reset a forgotten password from account settings."),
            ]
        )

        assert index.search("what is the weather on Mars") == []
        assert index.search("where is the employee handbook") == []
        assert index.search("what is the vacation policy")
