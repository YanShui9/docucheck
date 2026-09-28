"""Bounded local upload storage and structural preflight for untrusted files."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from pypdf import PdfReader

from ..parsing import MAX_FILE_BYTES, parse_xlsx


class UploadError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StoredUpload:
    document_id: str
    original_name: str
    storage_key: str
    source_sha256: str
    media_type: str
    pdf_pages: int
    path: Path


class UploadStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def save(self, file: UploadFile) -> StoredUpload:
        name = file.filename or ""
        if (not name or len(name) > 255 or name != name.strip()
                or any(char in name for char in "/\\:\x00")
                or any(ord(char) < 32 for char in name)):
            raise UploadError("invalid_filename")
        suffix = Path(name).suffix.lower()
        if suffix not in {".pdf", ".xlsx"}:
            raise UploadError("unsupported_file")

        document_id = uuid4().hex
        storage_key = f"uploads/{document_id[:2]}/{document_id}{suffix}"
        final_path = self.root / storage_key
        staging = self.root / "uploads" / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = staging / f"{document_id}{suffix}"
        digest = sha256()
        size = 0
        try:
            with temporary.open("xb") as target:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise UploadError("file_limit")
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise UploadError("invalid_file")
            if suffix == ".pdf":
                with temporary.open("rb") as source:
                    signature = source.read(5)
                if signature != b"%PDF-":
                    raise UploadError("invalid_file")
                try:
                    reader = PdfReader(temporary, strict=True)
                    pages = len(reader.pages)
                    if reader.is_encrypted or pages < 1:
                        raise UploadError("invalid_file")
                except UploadError:
                    raise
                except Exception as exc:
                    raise UploadError("invalid_file") from exc
                if pages > 30:
                    raise UploadError("case_page_limit")
                media_type = "application/pdf"
            else:
                try:
                    parse_xlsx(temporary, document_id=document_id)
                except Exception as exc:
                    raise UploadError("invalid_file") from exc
                pages = 0
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            temporary.replace(final_path)
            return StoredUpload(document_id, name, storage_key,
                                digest.hexdigest(), media_type, pages, final_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def discard(upload: StoredUpload) -> None:
        upload.path.unlink(missing_ok=True)
