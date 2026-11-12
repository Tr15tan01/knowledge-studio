"""File type detection from content (never trusting the client MIME type or name alone)."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

SUPPORTED_EXTENSIONS = {
    "pdf": "pdf",
    "txt": "txt",
    "text": "txt",
    "md": "markdown",
    "markdown": "markdown",
    "json": "json",
    "docx": "docx",
    "html": "html",
    "htm": "html",
    "csv": "csv",
}

MIME_FOR = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "markdown": "text/markdown",
    "json": "application/json",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html",
    "csv": "text/csv",
}

EXECUTABLE_SIGNATURES = (
    b"MZ",  # Windows PE
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",  # Mach-O
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
)

# DOCX zip-bomb defences.
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ZIP_RATIO = 150


class DetectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Detected:
    file_type: str
    mime_type: str
    extension: str


def extension_of(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def detect_from_head(filename: str, head: bytes) -> Detected:
    """Cheap validation on the first bytes, before the full file is processed."""
    ext = extension_of(filename)
    ftype = SUPPORTED_EXTENSIONS.get(ext)
    if not ftype:
        raise DetectionError("unsupported_format", "This file type isn't supported. Upload PDF, TXT, Markdown, JSON, DOCX, HTML or CSV.")
    if not head:
        raise DetectionError("empty_file", "The file is empty.")
    if any(head.startswith(sig) for sig in EXECUTABLE_SIGNATURES):
        raise DetectionError("executable_rejected", "Executable content is not allowed.")
    if ftype == "pdf":
        if b"%PDF-" not in head[:1024]:
            raise DetectionError("signature_mismatch", "This file does not look like a valid PDF.")
    elif ftype == "docx":
        if not head.startswith(b"PK\x03\x04"):
            raise DetectionError("signature_mismatch", "This file does not look like a valid DOCX document.")
    else:
        if head.startswith((b"%PDF-", b"PK\x03\x04")):
            raise DetectionError("signature_mismatch", "The file contents don't match its extension.")
        sample = head[:8192]
        if b"\x00" in sample and not sample.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise DetectionError("binary_content", "This file appears to contain binary data, not text.")
    return Detected(ftype, MIME_FOR[ftype], ext)


def validate_docx_bytes(data: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DetectionError("corrupt_file", "The DOCX file is corrupt.") from exc
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise DetectionError("archive_limits", "The DOCX file contains too many parts.")
    total = sum(i.file_size for i in infos)
    compressed = sum(i.compress_size for i in infos) or 1
    if total > MAX_ZIP_UNCOMPRESSED or total / compressed > MAX_ZIP_RATIO:
        raise DetectionError("archive_limits", "The DOCX file expands beyond safe limits.")
    names = {i.filename for i in infos}
    if "word/document.xml" not in names or "[Content_Types].xml" not in names:
        raise DetectionError("signature_mismatch", "This ZIP file is not a Word document.")
    if any(n.lower().endswith((".exe", ".dll", ".bin")) and not n.startswith("word/") for n in names):
        raise DetectionError("executable_rejected", "The document contains unsupported embedded content.")
    if "word/vbaProject.bin" in names:
        raise DetectionError("executable_rejected", "Macro-enabled documents are not supported.")


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        from charset_normalizer import from_bytes

        best = from_bytes(data[: 2 * 1024 * 1024]).best()
        if best is None:
            raise DetectionError("encoding_unknown", "The text encoding could not be determined.") from None
        return data.decode(best.encoding, errors="replace")


def validate_json_text(text: str) -> None:
    try:
        json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise DetectionError("corrupt_file", "The JSON file is not valid JSON.") from exc
