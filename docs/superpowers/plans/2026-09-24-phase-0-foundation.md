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
- [x] Add `.gitignore`, `backend/environment.yml` and `README.md`. The environment spec requests `python=3.12`; fixture dependencies are pinned, while application locks are still pending.
- [x] Run `conda env export -n docucheck --from-history` and compare it with `backend/environment.yml`; Python 3.12 was listed.
- [x] Run `git diff --check` and commit the verified baseline.

## Task 2: Synthetic source pack

- [x] Write `backend/tests/test_dev_pack.py` first. It checks one-page extractable PDFs, fixed workbook cells, eight fields, all five states, source locators and missing-warranty prerequisites.
- [x] Run `conda run -n docucheck python -m pytest backend/tests/test_dev_pack.py -q`; both tests failed because the pack did not exist.
- [x] Author the two PDF files from the fixed content in the phase 0 spec with embedded Chinese font and visible page numbers.
- [x] Author the XLSX with `@oai/artifact-tool`, using one `报价` sheet and literal values.
- [x] Add `labels.json` with eight results, precise evidence and processed-document prerequisite for the missing warranty.
- [x] Re-run the test: 2 passed. Render both PDF pages and the workbook; inspect legibility and source coordinates.
- [x] Commit the tested development pack and its builders (`11de584`).

## Task 3: Docker and MinerU feasibility

- [x] Preserve Docker diagnostics first. Record the current `sailor-ingest.sock` error and verify existing Docker data paths read-only.
- [x] Docker Engine is available. `docker info` reports Linux Engine 29.8.0; a temporary container wrote to the E-drive bind mount and Windows read the same contents back.
- [x] Pull project images and check MySQL 3307/Redis/worker health. An initial Docker Hub token request returned `EOF`; retry succeeded. Compose reports all three services healthy; the Linux worker responds to Celery ping and writes to the E-drive bind mount.
- [x] Install MinerU 4.0.7 in `docucheck-mineru` after a successful dependency dry run. Run Basic on both synthetic PDFs and compare twelve evidence fragments, page indices and bboxes with originals; 12/12 located.
- [x] Publish the current measurements and Docker blocker in `docs/environment/2026-09-24.md` and update `docs/development-roadmap.md`.

## Verification

- `conda run -n docucheck python --version` outputs 3.12.x.
- `conda run -n docucheck python -m pytest backend/tests/test_dev_pack.py -q` reports zero failures after the red test has been observed.
- `git diff --check` reports no whitespace errors.
- Docker, mount, service and MinerU checks are reported individually as pass/fail/blocked with exact evidence.
