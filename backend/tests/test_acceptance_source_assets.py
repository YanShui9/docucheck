"""Public originals must be reproducible without silently becoming business gold."""

import json
import zipfile
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

from evals.source_assets import AssetError, download_source, load_catalog, verify_assets


def make_catalog(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "originals"
    root.mkdir()
    source_path = root / "public-requirement.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(source_path)
    source = {
        "id": "public-requirement", "title": "Public requirement",
        "project_group": "public-project", "role": "requirement",
        "format": "pdf", "status": "captured", "published_on": None,
        "retrieved_on": "2026-10-01",
        "url": "https://download.ccgp.gov.cn/oss/download?uuid=test",
        "landing_url": "https://www.ccgp.gov.cn/example.htm",
        "filename": source_path.name,
        "sha256": sha256(source_path.read_bytes()).hexdigest(),
        "bytes": source_path.stat().st_size, "pdf_pages": 1,
        "constraints": ["Not yet labelled or sealed"],
    }
    catalog = {
        "schema_version": 1, "catalog_kind": "public_source_candidates",
        "researched_on": "2026-10-01", "sources": [source],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path, root, catalog


def save_catalog(path: Path, catalog: dict) -> None:
    path.write_text(json.dumps(catalog), encoding="utf-8")


def test_valid_assets_report_provenance_and_never_claim_business_acceptance(tmp_path: Path) -> None:
    path, root, _ = make_catalog(tmp_path)
    report = verify_assets(load_catalog(path), root)
    assert report["asset_integrity_passed"] is True
    assert report["verified"] == 1
    assert report["business_acceptance_measured"] is False
    assert report["sources"][0]["pdf_pages"] == 1


def test_tampered_source_is_an_error_and_never_overwritten_by_download(tmp_path: Path) -> None:
    path, root, _ = make_catalog(tmp_path)
    source = load_catalog(path).sources[0]
    file = root / source.filename
    file.write_bytes(b"%PDF-tampered")
    report = verify_assets(load_catalog(path), root)
    assert report["asset_integrity_passed"] is False
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("network used"))) as client,
        pytest.raises(AssetError),
    ):
        download_source(source, root, client)
    assert file.read_bytes() == b"%PDF-tampered"


@pytest.mark.parametrize("filename", ["../outside.pdf", "C:/outside.pdf", "nested/source.pdf"])
def test_catalog_rejects_paths_outside_original_asset_name(tmp_path: Path, filename: str) -> None:
    path, _, catalog = make_catalog(tmp_path)
    catalog["sources"][0]["filename"] = filename
    save_catalog(path, catalog)
    with pytest.raises(ValueError):
        load_catalog(path)


@pytest.mark.parametrize("url", ["http://download.ccgp.gov.cn/a", "https://127.0.0.1/a", "https://u:p@www.ccgp.gov.cn/a"])
def test_catalog_rejects_non_public_origin_and_embedded_credentials(tmp_path: Path, url: str) -> None:
    path, _, catalog = make_catalog(tmp_path)
    catalog["sources"][0]["url"] = url
    save_catalog(path, catalog)
    with pytest.raises(ValueError):
        load_catalog(path)


def test_unknown_gold_or_review_fields_cannot_be_added_to_source_catalog(tmp_path: Path) -> None:
    path, _, catalog = make_catalog(tmp_path)
    catalog["sources"][0]["expected_status"] = "satisfied"
    save_catalog(path, catalog)
    with pytest.raises(ValueError):
        load_catalog(path)


def test_duplicate_ids_and_filename_aliases_are_rejected(tmp_path: Path) -> None:
    path, _, catalog = make_catalog(tmp_path)
    catalog["sources"].append(deepcopy(catalog["sources"][0]))
    save_catalog(path, catalog)
    with pytest.raises(ValueError):
        load_catalog(path)


def test_unavailable_attachment_keeps_explicit_failure_without_fake_binary(tmp_path: Path) -> None:
    path, root, catalog = make_catalog(tmp_path)
    source = catalog["sources"][0]
    source.update(status="unavailable", filename=None, sha256=None, bytes=None, pdf_pages=None)
    save_catalog(path, catalog)
    report = verify_assets(load_catalog(path), root)
    assert report["verified"] == 0
    assert report["unavailable"] == 1
    assert report["sources"][0]["status"] == "unavailable"


def test_pdf_page_count_and_upload_scope_are_checked_separately(tmp_path: Path) -> None:
    path, root, catalog = make_catalog(tmp_path)
    source = catalog["sources"][0]
    writer = PdfWriter()
    for _ in range(31):
        writer.add_blank_page(width=595, height=842)
    file = root / source["filename"]
    writer.write(file)
    source.update(bytes=file.stat().st_size, sha256=sha256(file.read_bytes()).hexdigest(), pdf_pages=31)
    save_catalog(path, catalog)
    report = verify_assets(load_catalog(path), root)
    assert report["asset_integrity_passed"] is True
    assert report["sources"][0]["within_single_file_upload_limits"] is False
    source["pdf_pages"] = 30
    save_catalog(path, catalog)
    assert verify_assets(load_catalog(path), root)["sources"][0]["error"] == "page_count_mismatch"


def test_html_even_with_a_matching_hash_is_not_a_pdf(tmp_path: Path) -> None:
    path, root, catalog = make_catalog(tmp_path)
    content = b"<html>download verification</html>"
    source = catalog["sources"][0]
    (root / source["filename"]).write_bytes(content)
    source.update(bytes=len(content), sha256=sha256(content).hexdigest())
    save_catalog(path, catalog)
    assert verify_assets(load_catalog(path), root)["sources"][0]["error"] == "not_pdf"


def test_redirect_to_unlisted_host_is_rejected_before_request(tmp_path: Path) -> None:
    path, root, _ = make_catalog(tmp_path)
    source = load_catalog(path).sources[0]
    (root / source.filename).unlink()
    requests = []

    def transport(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    with (
        httpx.Client(transport=httpx.MockTransport(transport)) as client,
        pytest.raises(AssetError, match="unapproved_source_url"),
    ):
        download_source(source, root, client)
    assert len(requests) == 1
    assert not (root / source.filename).exists()


def test_reproducible_download_checks_signature_hash_and_cleans_failed_staging(tmp_path: Path) -> None:
    path, root, _ = make_catalog(tmp_path)
    source = load_catalog(path).sources[0]
    file = root / source.filename
    content = file.read_bytes()
    file.unlink()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as client:
        download_source(source, root, client)
    assert file.read_bytes() == content
    file.unlink()
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"bad"))) as client,
        pytest.raises(AssetError),
    ):
        download_source(source, root, client)
    assert list(root.iterdir()) == []


def test_zip_with_path_escape_is_rejected_without_extraction(tmp_path: Path) -> None:
    path, root, catalog = make_catalog(tmp_path)
    file = root / "public-requirement.zip"
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr("../outside.pdf", b"data")
    source = catalog["sources"][0]
    source.update(format="zip", filename=file.name, pdf_pages=None,
                  bytes=file.stat().st_size, sha256=sha256(file.read_bytes()).hexdigest())
    save_catalog(path, catalog)
    assert verify_assets(load_catalog(path), root)["sources"][0]["error"] == "unsafe_archive_member"
    assert not (tmp_path / "outside.pdf").exists()
