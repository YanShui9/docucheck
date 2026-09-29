# M3-11b Correction Submission Implementation Plan

> **For agentic workers:** Implement inline with test-first checkpoints. This step shares the existing repository and does not require subagents.

**Goal:** Submit a source-backed fact value correction as a new immutable Case revision with retrievable audit and old Run invalidation.

**Architecture:** Validate one persisted FieldFact or memory FactSet from the current Run, then use a Case row lock and a single transaction to clone the parsed snapshot, map evidence IDs, append the correction, and advance the revision. Existing Run reads compute `is_current` from the Case revision.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, Alembic, MySQL/SQLite, pytest.

---

### Task 1: Red tests for correction transaction

**Files:** `backend/tests/test_corrections.py`

- [x] Build an SQLite API fixture with one XLSX cell containing two source-backed values, upload and parse it, and save a completed Run with one verified fact set.
- [x] Add a POST test requiring a new revision, copied evidence with distinct IDs, unchanged original snapshot, one audit row and `is_current=false` on the source Run. Verify same-key replay, changed-key payload rejection and stale `expected_revision`.
- [x] Add invalid-source, unknown-fact, active-Run, legacy/tampered memory and cross-Case tests; observe expected failures before implementation.

### Task 2: Schema and repository

**Files:** `backend/app/db/models.py`, `backend/migrations/versions/0006_corrections.py`, `backend/app/db/correction_repository.py`

- [x] Define `corrections` with Case, source Run, old/new snapshot, fact and evidence mappings, old/new candidate, reason and timestamp. Upgrade/downgrade the migration; run the existing schema comparison test.
- [x] Implement `CorrectionRepository.submit` with input validation, replay-first idempotency, Case row lock, current Run/fact/active-Run checks, source candidate validation and a bounded document/evidence clone. Commit revision, correction and idempotency in one transaction. Use controlled error codes.
- [x] Run targeted tests until green; verify same-key replay and different-key MySQL race. New corrected revision refuses Run submission while rules have not been recomputed.

### Task 3: HTTP routes and validation

**Files:** `backend/app/api/schemas.py`, `backend/app/api/main.py`, `backend/tests/test_corrections.py`

- [x] Add strict correction request/receipt schemas, GET Run facts, POST Case corrections and GET audit detail with required `Idempotency-Key`. Map expected domain errors to 404, 409 or 422.
- [x] Run the API tests and entire backend suite (`387 passed, 11 skipped`), Ruff, pip check and `git diff --check`.

### Task 4: Live migration and documentation

**Files:** `CLAUDE.md`, `README.md`, `docs/development-roadmap.md`, `docs/m3-11b-correction-submission-2026-09-29.md`

- [x] Before Docker operations, notify the user; stop automatic dispatcher/worker, migrate MySQL, run a scripted MySQL test without DeepSeek (`1 passed`), rebuild/restart worker and dispatcher, verify health. Rebuild once more after the final repository guard change.
- [x] Record exact pass counts and limits. Distinguish accepted correction from not-yet-recomputed formal result. Review and commit the milestone.
