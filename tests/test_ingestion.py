from pathlib import Path

import pytest

from localrag.ingestion import IngestionError, ingest_directory, read_document


def test_read_document_returns_utf8_content_and_resolved_path(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# Local notes\n\nPrivate content.", encoding="utf-8")

    document = read_document(source)

    assert document.path == source.resolve()
    assert document.content == "# Local notes\n\nPrivate content."


def test_read_document_accepts_utf8_bom(tmp_path: Path) -> None:
    source = tmp_path / "bom.txt"
    source.write_text("BOM content", encoding="utf-8-sig")

    assert read_document(source).content == "BOM content"


def test_pdf_extraction_marks_page_boundaries(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    source = tmp_path / "pages.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    with source.open("wb") as output:
        writer.write(output)

    content = read_document(source).content

    assert "[Page 1]" in content
    assert "[Page 2]" in content


def test_read_html_extracts_text_and_ignores_script_content(tmp_path: Path) -> None:
    source = tmp_path / "page.html"
    source.write_text(
        "<html><title>Policy</title><body><p>Annual leave</p>"
        "<script>do not index this secret</script></body></html>",
        encoding="utf-8",
    )

    content = read_document(source).content

    assert "Annual leave" in content
    assert "secret" not in content


def test_read_csv_extracts_cells(tmp_path: Path) -> None:
    source = tmp_path / "table.csv"
    source.write_text("name,days\nAnnual leave,15\n", encoding="utf-8")

    content = read_document(source).content

    assert "Annual leave | 15" in content


def test_read_xlsx_extracts_sheet_names_and_values(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "table.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Policies"
    sheet.append(["Type", "Days"])
    sheet.append(["Annual leave", 15])
    workbook.save(source)

    content = read_document(source).content

    assert "[Policies]" in content
    assert "Annual leave | 15" in content


def test_read_pptx_extracts_slide_text(tmp_path: Path) -> None:
    pptx = pytest.importorskip("pptx")
    source = tmp_path / "deck.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = slide.shapes.add_textbox(0, 0, 300, 100)
    box.text = "Annual leave policy"
    presentation.save(source)

    content = read_document(source).content

    assert "[Slide 1]" in content
    assert "Annual leave policy" in content


@pytest.mark.parametrize("filename", ["image.png", "no_extension"])
def test_read_document_rejects_unsupported_types(tmp_path: Path, filename: str) -> None:
    source = tmp_path / filename
    source.write_text("content", encoding="utf-8")

    with pytest.raises(IngestionError, match="Unsupported document type"):
        read_document(source)


def test_read_document_rejects_empty_content(tmp_path: Path) -> None:
    source = tmp_path / "empty.txt"
    source.write_text("  \n", encoding="utf-8")

    with pytest.raises(IngestionError, match="Document is empty"):
        read_document(source)


def test_read_document_rejects_extracted_content_over_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "long.md"
    source.write_text("012345", encoding="utf-8")
    monkeypatch.setattr("localrag.ingestion.MAX_DOCUMENT_CHARACTERS", 5)

    with pytest.raises(IngestionError, match="too large after extraction"):
        read_document(source)


def test_ingest_directory_recurses_in_path_order(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "nested" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "ignored.rtf").write_text("ignored", encoding="utf-8")

    documents = ingest_directory(tmp_path)

    assert [document.path.name for document in documents] == ["a.md", "z.txt"]


def test_read_document_reports_missing_pdf_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "notes.pdf"
    source.write_bytes(b"not a real PDF")

    real_import = __import__

    def import_without_pypdf(name: str, *args: object, **kwargs: object) -> object:
        if name == "pypdf":
            raise ImportError("test dependency unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_without_pypdf)

    with pytest.raises(IngestionError, match="PDF support requires"):
        read_document(source)
