"""Reproduce public research assets; never ingest gold or invoke a model."""

import argparse
import json
import os
import re
import tempfile
import zipfile
from datetime import date
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from pypdf import PdfReader

APPROVED_HOSTS = frozenset({
    "www.ccgp.gov.cn", "download.ccgp.gov.cn", "www.ccgp-shaanxi.gov.cn",
    "gongyi.zfcg.henan.gov.cn", "jpg.zfcg.sh.gov.cn", "www.glc.edu.cn",
})
MAX_RESEARCH_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_CATALOG = Path(__file__).parent / "acceptance" / "source_catalog.json"


class AssetError(ValueError):
    """Stable local acquisition/validation error without leaking request details."""


def validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        approved = (
            parsed.scheme == "https" and parsed.hostname in APPROVED_HOSTS
            and parsed.port in {None, 443} and not parsed.username
            and not parsed.password and not parsed.fragment
        )
    except ValueError:
        approved = False
    if not approved:
        raise AssetError("unapproved_source_url")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    title: str = Field(min_length=1, max_length=300)
    project_group: str = Field(min_length=1, max_length=150)
    role: Literal[
        "requirement", "technical_response", "award_detail", "requirement_archive",
        "framework_detail", "blank_quote_template",
    ]
    format: Literal["pdf", "xlsx", "zip"]
    status: Literal["captured", "unavailable"]
    published_on: date | None
    retrieved_on: date
    landing_url: str = Field(max_length=2000)
    url: str = Field(max_length=2000)
    filename: str | None
    sha256: str | None
    bytes: StrictInt | None = Field(ge=1, le=MAX_RESEARCH_BYTES)
    pdf_pages: StrictInt | None = Field(ge=1, le=4096)
    constraints: list[str] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def check_provenance(self) -> "Source":
        validate_url(self.url)
        validate_url(self.landing_url)
        if self.published_on and self.published_on > self.retrieved_on:
            raise ValueError("source_date_order")
        if self.status == "captured":
            if self.filename != f"{self.id}.{self.format}":
                raise ValueError("invalid_asset_filename")
            if not self.sha256 or not re.fullmatch(r"[0-9a-f]{64}", self.sha256) or not self.bytes:
                raise ValueError("missing_capture_digest")
            if (self.format == "pdf") != (self.pdf_pages is not None):
                raise ValueError("invalid_page_metadata")
        elif any(item is not None for item in (self.filename, self.sha256, self.bytes, self.pdf_pages)):
            raise ValueError("uncaptured_source_has_asset_metadata")
        return self


class SourceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    catalog_kind: Literal["public_source_candidates"]
    researched_on: date
    sources: list[Source] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_identity(self) -> "SourceCatalog":
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("duplicate_source_id")
        filenames = [source.filename for source in self.sources if source.filename]
        if len(set(filenames)) != len(filenames):
            raise ValueError("duplicate_asset_filename")
        if any(source.retrieved_on > self.researched_on for source in self.sources):
            raise ValueError("catalog_date_order")
        return self


def load_catalog(path: Path) -> SourceCatalog:
    if path.stat().st_size > 1024 * 1024:
        raise AssetError("catalog_limit")
    return SourceCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def asset_path(source: Source, root: Path) -> Path:
    if not source.filename:
        raise AssetError("source_not_captured")
    root = root.resolve()
    path = root / source.filename
    if not path.resolve().is_relative_to(root):
        raise AssetError("asset_path_escape")
    return path


def inspect_asset(source: Source, path: Path) -> dict:
    size = path.stat().st_size
    if size != source.bytes:
        raise AssetError("byte_count_mismatch")
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != source.sha256:
        raise AssetError("sha256_mismatch")
    details = {"bytes": size, "sha256": digest.hexdigest()}
    if source.format == "pdf":
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise AssetError("not_pdf")
        try:
            reader = PdfReader(path, strict=True)
            if reader.is_encrypted:
                raise AssetError("encrypted_pdf")
            pages = len(reader.pages)
        except AssetError:
            raise
        except Exception as exc:
            raise AssetError("invalid_pdf") from exc
        if pages != source.pdf_pages:
            raise AssetError("page_count_mismatch")
        details.update(pdf_pages=pages,
                       within_single_file_upload_limits=size <= MAX_UPLOAD_BYTES and pages <= 30)
    else:
        try:
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > MAX_RESEARCH_BYTES:
                    raise AssetError("archive_limit")
                for item in entries:
                    name = item.filename.replace("\\", "/")
                    if (PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
                            or ".." in PurePosixPath(name).parts or "\x00" in name
                            or ":" in name):
                        raise AssetError("unsafe_archive_member")
                if source.format == "xlsx" and not {
                    "[Content_Types].xml", "xl/workbook.xml",
                }.issubset(archive.namelist()):
                    raise AssetError("not_xlsx")
                if archive.testzip() is not None:
                    raise AssetError("archive_crc_error")
                details.update(archive_members=len(entries),
                               within_single_file_upload_limits=(
                                   source.format == "xlsx" and size <= MAX_UPLOAD_BYTES
                               ))
        except AssetError:
            raise
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
            raise AssetError("invalid_archive") from exc
    return details


def download_source(source: Source, root: Path, client: httpx.Client) -> None:
    """Download only known public originals, validating before exclusive publication."""
    path = asset_path(source, root)
    if path.exists():
        inspect_asset(source, path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".source-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            url = source.url
            for _ in range(5):
                validate_url(url)
                with client.stream("GET", url, follow_redirects=False) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise AssetError("invalid_redirect")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > source.bytes or size > MAX_RESEARCH_BYTES:
                            raise AssetError("download_limit")
                        output.write(chunk)
                    break
            else:
                raise AssetError("redirect_limit")
        inspect_asset(source, temporary)
        # A concurrent download or user file must never be overwritten.
        os.link(temporary, path)
    except httpx.HTTPError as exc:
        raise AssetError("source_download_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def verify_assets(catalog: SourceCatalog, root: Path) -> dict:
    sources = []
    for source in catalog.sources:
        result = {
            "id": source.id, "status": source.status, "role": source.role,
            "project_group": source.project_group,
        }
        if source.status == "captured":
            try:
                path = asset_path(source, root)
                result.update(inspect_asset(source, path), status="verified", path=str(path.resolve()))
            except AssetError as exc:
                result.update(status="error", error=str(exc))
            except OSError:
                result.update(status="error", error="asset_missing_or_unreadable")
        sources.append(result)
    verified = sum(item["status"] == "verified" for item in sources)
    errors = sum(item["status"] == "error" for item in sources)
    return {
        "scope": "public_source_asset_integrity",
        "asset_integrity_passed": errors == 0,
        "business_acceptance_measured": False,
        "verified": verified, "errors": errors,
        "unavailable": sum(item["status"] == "unavailable" for item in sources),
        "notes": ["Not a labelled or sealed corpus; combined Case limits are not evaluated.",
                  "XLSX container integrity does not prove business parsing coverage."],
        "sources": sources,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--root", type=Path, default=Path("data/m5-source-research"))
    parser.add_argument("--download", action="store_true",
                        help="Explicitly download captured originals from listed public websites.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
    except (ValueError, OSError):
        print(json.dumps({"error": "invalid_source_catalog"}))
        return 1
    acquisition_errors = []
    if args.download:
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=25) as client:
            for source in catalog.sources:
                if source.status == "captured":
                    try:
                        download_source(source, args.root, client)
                    except (AssetError, OSError) as exc:
                        acquisition_errors.append({"id": source.id,
                                                   "error": str(exc) if isinstance(exc, AssetError)
                                                   else "asset_storage_failed"})
    report = verify_assets(catalog, args.root)
    if acquisition_errors:
        report.update(asset_integrity_passed=False, acquisition_errors=acquisition_errors)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["asset_integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
