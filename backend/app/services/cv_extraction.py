"""CV file validation and text extraction (PDF, DOCX).

Files are identified by content (magic bytes), never by the client's filename or content type.
Scanned PDFs without a text layer are accepted but flagged; OCR can be added here later.
"""

import io
import zipfile
from dataclasses import dataclass

from docx import Document
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PAGES = 30
MAX_TEXT_CHARS = 60_000
MAX_DOCX_UNCOMPRESSED = 50 * 1024 * 1024  # zip-bomb guard


class UnsupportedDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedDocument:
    extension: str
    text: str
    pages: int | None


def detect_type(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if "word/document.xml" not in names:
                    raise UnsupportedDocumentError("only PDF and Word (.docx) CVs are accepted")
                if any(n.startswith("word/vbaProject") for n in names):
                    raise UnsupportedDocumentError("macro-enabled documents are not accepted")
                if sum(i.file_size for i in archive.infolist()) > MAX_DOCX_UNCOMPRESSED:
                    raise UnsupportedDocumentError("document is too large when uncompressed")
        except zipfile.BadZipFile as exc:
            raise UnsupportedDocumentError("the Word document is corrupted") from exc
        return "docx"
    raise UnsupportedDocumentError("only PDF and Word (.docx) CVs are accepted")


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_TEXT_CHARS]


def extract(data: bytes) -> ExtractedDocument:
    kind = detect_type(data)
    if kind == "pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise UnsupportedDocumentError("password-protected PDFs are not accepted")
            pages = reader.pages[:MAX_PAGES]
            text = "\n".join((page.extract_text() or "") for page in pages)
        except PdfReadError as exc:
            raise UnsupportedDocumentError("the PDF could not be read") from exc
        return ExtractedDocument(extension="pdf", text=_clean(text), pages=len(reader.pages))

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several exception types for corrupt files
        raise UnsupportedDocumentError("the Word document could not be read") from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return ExtractedDocument(extension="docx", text=_clean("\n".join(parts)), pages=None)
