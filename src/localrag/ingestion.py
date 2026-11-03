"""Read the file types LocalRAG supports."""

import csv
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED_SUFFIXES = frozenset({".csv", ".docx", ".html", ".htm", ".md", ".pdf", ".pptx", ".txt", ".xlsx"})
MAX_DOCUMENT_CHARACTERS = 5_000_000


class IngestionError(ValueError):
    """Raised when a document cannot be read."""


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_tags: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self._ignored_tags.append(tag.casefold())

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tags and tag.casefold() == self._ignored_tags[-1]:
            self._ignored_tags.pop()

    def handle_data(self, data: str) -> None:
        if not self._ignored_tags and data.strip():
            self.parts.append(data.strip())


@dataclass(frozen=True, slots=True)
class Document:
    """A document and its path."""

    path: Path
    content: str


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as error:
        raise IngestionError(
            "PDF support requires the optional document dependencies; "
            "install with: pip install 'localrag[documents]'"
        ) from error

    try:
        reader = PdfReader(str(path))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except (OSError, PdfReadError, ValueError) as error:
        raise IngestionError(f"Could not read PDF document: {path}") from error


def _read_docx(path: Path) -> str:
    try:
        from docx import Document as WordDocument
        from docx.opc.exceptions import PackageNotFoundError
    except ImportError as error:
        raise IngestionError(
            "Word support requires the optional document dependencies; "
            "install with: pip install 'localrag[documents]'"
        ) from error

    try:
        word_document = WordDocument(str(path))
        paragraphs = [paragraph.text for paragraph in word_document.paragraphs]
        tables = [
            " | ".join(cell.text for cell in row.cells)
            for table in word_document.tables
            for row in table.rows
        ]
        return "\n".join(paragraphs + tables)
    except (OSError, PackageNotFoundError, ValueError) as error:
        raise IngestionError(f"Could not read Word document: {path}") from error


def _read_html(path: Path) -> str:
    try:
        parser = _TextHTMLParser()
        parser.feed(path.read_text(encoding="utf-8-sig"))
        parser.close()
        return "\n".join(parser.parts)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise IngestionError(f"Could not read HTML document: {path}") from error


def _read_csv(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            rows = csv.reader(source)
            return "\n".join(" | ".join(row) for row in rows)
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise IngestionError(f"Could not read CSV document: {path}") from error


def _read_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise IngestionError(
            "Excel support requires the optional document dependencies; "
            "install with: pip install 'localrag[documents]'"
        ) from error
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            return "\n\n".join(
                f"[{sheet.title}]\n" + "\n".join(
                    " | ".join("" if value is None else str(value) for value in row)
                    for row in sheet.iter_rows(values_only=True)
                )
                for sheet in workbook.worksheets
            )
        finally:
            workbook.close()
    except (OSError, ValueError, KeyError) as error:
        raise IngestionError(f"Could not read Excel document: {path}") from error


def _read_pptx(path: Path) -> str:
    try:
        from pptx import Presentation
    except ImportError as error:
        raise IngestionError(
            "PowerPoint support requires the optional document dependencies; "
            "install with: pip install 'localrag[documents]'"
        ) from error
    try:
        presentation = Presentation(str(path))
        slides: list[str] = []
        for number, slide in enumerate(presentation.slides, start=1):
            parts = [shape.text for shape in slide.shapes if getattr(shape, "has_text_frame", False) and shape.text.strip()]
            parts.extend(
                " | ".join(cell.text for cell in row.cells)
                for shape in slide.shapes
                if getattr(shape, "has_table", False)
                for row in shape.table.rows
            )
            slides.append(f"[Slide {number}]\n" + "\n".join(parts))
        return "\n\n".join(slides)
    except (OSError, ValueError, KeyError) as error:
        raise IngestionError(f"Could not read PowerPoint document: {path}") from error


def read_document(path: Path) -> Document:
    """Read one supported document."""
    path = path.expanduser()

    if not path.is_file():
        raise IngestionError(f"Document does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise IngestionError(f"Unsupported document type: {path.suffix or '(none)'}; use {supported}")

    if suffix == ".pdf":
        content = _read_pdf(path)
    elif suffix == ".docx":
        content = _read_docx(path)
    elif suffix in {".html", ".htm"}:
        content = _read_html(path)
    elif suffix == ".csv":
        content = _read_csv(path)
    elif suffix == ".xlsx":
        content = _read_xlsx(path)
    elif suffix == ".pptx":
        content = _read_pptx(path)
    else:
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            raise IngestionError(f"Document is not valid UTF-8: {path}") from error
        except OSError as error:
            raise IngestionError(f"Could not read document: {path}") from error

    if not content.strip():
        raise IngestionError(f"Document is empty: {path}")
    if len(content) > MAX_DOCUMENT_CHARACTERS:
        raise IngestionError(
            f"Document is too large after extraction: {path} exceeds "
            f"{MAX_DOCUMENT_CHARACTERS:,} characters."
        )

    return Document(path=path.resolve(), content=content)


def ingest_directory(directory: Path) -> list[Document]:
    """Read supported documents under a folder."""
    directory = directory.expanduser()

    if not directory.is_dir():
        raise IngestionError(f"Document directory does not exist: {directory}")

    documents: list[Document] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            documents.append(read_document(path))

    return documents
