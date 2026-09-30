# M3-12b Case Cleanup Implementation Plan

> **For agentic workers:** Execute this milestone inline. Preserve the existing uncommitted implementation, review it against the spec, and complete missing tests and fixes before committing. No subagents are required.

**Goal:** Make a requested Case deletion retryable across database and file-system failures without deleting originals still referenced by another Case.

**Architecture:** A Case row lease owns one cleanup attempt. A durable per-Case file ledger survives deletion of document rows and process restarts. The API exposes deletion progress while one cleanup command performs bounded work.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, Alembic, MySQL 8.4, SQLite, pytest.

---

### Task 1: Verify the existing implementation

**Files:** `backend/app/db/case_cleanup.py`, `backend/tests/test_case_cleanup.py`, `backend/tests/test_case_cleanup_mysql.py`.

- [x] Run focused tests and full offline suite; record current failures and coverage.
- [x] Test lease expiry during a long cleanup attempt and verify stale workers cannot finalize results.
- [x] Test a late Agent result after the Case database records have been removed; M3-12a already covers late parse claims after deletion request.
- [x] Review all Case foreign keys, idempotency scopes, file path validation and cross-Case reference logic.

### Task 2: Complete cleanup persistence and API contract

**Files:** `backend/app/db/models.py`, `backend/migrations/versions/0009_case_cleanup.py`, `backend/app/db/case_deletion.py`, `backend/app/api/main.py`, `backend/app/api/schemas.py`.

- [x] Verify migration upgrade/downgrade and metadata comparison.
- [x] Verify the deletion status endpoint and 200/202/410/409 responses at each stage.
- [x] Fix uncovered claim expiry and queue index gaps with failing tests first.

### Task 3: Validate and document

**Files:** `backend/app/cleanup_cases.py`, `compose.yaml`, `README.md`, `CLAUDE.md`, `docs/development-roadmap.md`, `docs/m3-12b-case-cleanup-2026-09-30.md`.

- [x] Run full offline tests, Ruff, pip check and `git diff --check`.
- [x] Notify before MySQL access; apply migration, run only synthetic MySQL cleanup tests, and inspect real pending deletion count. Long-running cleaner activation remains a separate approval step.
- [x] Document limits, results and service activation state; commit this milestone.
