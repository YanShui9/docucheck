# M3-11c Correction Rule Recheck Implementation Plan

> **For agentic workers:** Implement this single milestone inline with test-first checkpoints. No subagents are required.

**Goal:** Revalidate corrected facts in the new snapshot and persist one deterministic, source-linked rule result per correction.

**Architecture:** A pure rebind function maps a verified fact set to the cloned evidence and reruns source validation and field rules. A repository computes outside a write transaction, then checks the current Case under row lock and inserts one immutable review. The API exposes a repeatable POST and review detail.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, Alembic, MySQL/SQLite, pytest.

---

### Task 1: Failing tests and rule contract

**Files:** `backend/tests/test_correction_recheck.py`, existing correction fixtures.

- [x] Test partial fact set: recheck stores `processing_status=partial`, no business status, `needs_review`, and distinct new evidence/fact IDs.
- [x] Test complete fact set: all facts rebind, both source-backed values remain, and current rule yields a conservative business status. Test idempotent repeat and historical version rejection.
- [x] Test tampered new evidence, stale source memory, altered audit mapping and concurrent same-correction requests fail closed or produce one review.
- [x] Run focused tests and observe the missing route/table failure before implementation.

### Task 2: Evidence mapping and persistence

**Files:** `backend/app/db/correction_recheck.py`, `backend/app/db/models.py`, `backend/migrations/versions/0007_correction_reviews.py`.

- [x] Reuse source fact-set validation; map all scanned evidence and facts, revalidate raw source and preserved semantics in the new snapshot, and call the existing field/memory rule.
- [x] Store review, corrected fact and result with a unique correction ID. Compute before the Case lock, then recheck current revision and prior review in the transaction.
- [x] Run focused and schema upgrade/downgrade tests.

### Task 3: API and acceptance

**Files:** `backend/app/api/main.py`, `backend/app/api/schemas.py`, `backend/app/db/correction_repository.py`, documentation.

- [x] Add synchronous recheck POST and dynamic correction detail; preserve original submission receipt's `pending_recheck` replay behavior.
- [x] Verify full offline tests, Ruff, pip check, migration and opt-in scripted MySQL test. Notify before Docker actions; do not call DeepSeek.
- [x] Record the no-model, no-front-end boundary and commit the milestone.
