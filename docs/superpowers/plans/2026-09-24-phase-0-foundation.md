# DocuCheck Phase 0 Implementation Plan

> **For agentic workers:** Execute the checked steps in order and record command output. This phase produces a development fixture and environment evidence, not the application.

**Goal:** Establish a reproducible Python 3.12 baseline and one source-backed PDF/XLSX development pack covering all five business states.

**Architecture:** Keep synthetic fixture authoring separate from application code. Store source documents and expected outcomes together; future parser and rule tests read those documents as inputs while keeping labels outside the runtime path. Validate Docker and MinerU independently, recording failures without replacing the chosen stack.

**Tech Stack:** Conda Python 3.12, ReportLab, pypdf, `@oai/artifact-tool`, Git, Docker Desktop, MinerU 4.x.

---

## File map

- `README.md`: current project state and local setup.
- `.gitignore`: local credentials, caches and generated temp files.
- `backend/environment.yml`: Python environment specification.
- `examples/dev-pack-001/requirements.pdf`: synthetic requirement document.
- `examples/dev-pack-001/vendor-a-quote.xlsx`: synthetic quote.
- `examples/dev-pack-001/vendor-a-spec.pdf`: synthetic product specification.
- `examples/dev-pack-001/labels.json`: eight expected states with precise original locations.
- `examples/dev-pack-001/README.md`: fixture provenance and intent.
- `scripts/build_dev_pdfs.py`: deterministic PDF fixture builder from fixed text.
- `scripts/build_dev_quote.mjs`: XLSX fixture builder from fixed cell values.
- `backend/tests/test_dev_pack.py`: cross-check extraction and labels against original files.
- `docs/environment/2026-09-24.md`: commands, versions, Docker diagnostics and MinerU measurements.

## Task 1: Repository and Python baseline

- [x] Create the project Git repository and commit the approved phase 0 design on `main`.
- [x] Switch to `phase-0-foundation` for implementation.
- [x] Create the `docucheck` conda environment; `conda run -n docucheck python --version` returned `Python 3.12.14`.
- [ ] Add `.gitignore`, `backend/environment.yml` and `README.md`. The environment spec must request `python=3.12` without claiming that dependency locks are complete.
- [ ] Run `conda env export -n docucheck --from-history` and compare it with `backend/environment.yml`; expected: Python 3.12 is listed.
- [ ] Run `git diff --check` and commit the verified baseline.

## Task 2: Synthetic source pack

- [ ] Write `backend/tests/test_dev_pack.py` first. It must assert that the two PDFs each have one page with extractable Chinese text, that the workbook has a `报价` sheet with fixed model, memory, quantity, price and delivery cells, and that `labels.json` has exactly the eight named fields and the expected state distribution (4 满足, 1 不满足, 1 资料冲突, 1 证据不足, 1 待人工确认).
- [ ] Run `conda run -n docucheck python -m pytest backend/tests/test_dev_pack.py -q`; expected: tests fail because the pack does not exist.
- [ ] Author the two PDF files from the fixed content in the phase 0 spec. Embed a Chinese font, keep each source statement on its own line, and print a visible page number.
- [ ] Author the XLSX with `@oai/artifact-tool`, using one `报价` sheet and literal values. Save exact cell coordinates in `labels.json`.
- [ ] Add `labels.json` with one result per field, the requirement and supplier evidence locators, reason, and processed-document prerequisite for the missing warranty.
- [ ] Re-run the test; expected: all fixture assertions pass. Render both PDF pages and the workbook, inspect legibility, and verify source strings/coordinates manually.
- [ ] Commit the tested development pack and its builders.

## Task 3: Docker and MinerU feasibility

- [ ] Preserve Docker diagnostics first. Record the current `sailor-ingest.sock` error and verify existing Docker data paths read-only.
- [ ] If Docker Engine can be restored without deleting existing data, run `docker info`, then a small container with an E-drive bind mount and check round-trip content. Only then create Compose services and check MySQL 3307/Redis/worker health.
- [ ] If Engine remains unavailable, record a failed stage 0 Docker gate and continue the independent fixture and Python work. Do not report container validation as passed.
- [ ] Install a pinned MinerU 4.x candidate in the isolated environment only after dependency compatibility is checked. Run Basic against the synthetic PDF files; record command, version, runtime, text extraction and page positions. Compare with the original PDFs.
- [ ] Publish the measurements and remaining blockers in `docs/environment/2026-09-24.md` and update `docs/development-roadmap.md` checkboxes.

## Verification

- `conda run -n docucheck python --version` outputs 3.12.x.
- `conda run -n docucheck python -m pytest backend/tests/test_dev_pack.py -q` reports zero failures after the red test has been observed.
- `git diff --check` reports no whitespace errors.
- Docker, mount, service and MinerU checks are reported individually as pass/fail/blocked with exact evidence.
