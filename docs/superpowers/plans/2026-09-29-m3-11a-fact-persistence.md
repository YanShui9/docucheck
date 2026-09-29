# M3-11a Validated Fact Persistence Implementation Plan

> **For agentic workers:** Execute this plan inline, one checked task at a time. No subagent is required for this single shared-state change.

**Goal:** Retain bounded, source-scoped facts from an Agent Run so a later human correction can be checked and recomputed without inventing prior facts.

**Architecture:** Extend the existing `agent_runs.memory_json` payload to version 2. Serialization selects the latest fact set per supplier/field and enforces size limits; restoration validates identities, source scope and normalized field values before exposing the sets to tools. Version 1 Run memory remains readable.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy JSON, pytest, Ruff.

---

### Task 1: Define an exact fact-set codec

**Files:** `backend/app/agent/fact_memory.py`, `backend/tests/test_fact_memory.py`

- [x] Write a failing round-trip test with one `FieldFactSet` built from a verified `FieldCandidate` and one legacy memory `FactSet`; expect `encode_fact_sets` to return JSON-safe values and `decode_fact_sets` to restore immutable dataclasses.
- [x] Run `python.exe -m pytest backend/tests/test_fact_memory.py -q`; expect ImportError or missing function failure.
- [x] Implement `encode_fact_sets(repo, fact_sets)` and `decode_fact_sets(repo, data)`. Store `kind`, `fact_set_id`, `supplier`, `field_key`, `scanned_ids`, `complete`, `coverage_issues` and all fact fields; decimal values become strings. Decode with strict key/type checks and `repo.read` scope validation. For `FieldFact`, rebuild `FieldCandidate` from raw fields, call the source candidate validator, and require the rebuilt fact to equal the saved fact. For legacy `VerifiedFact`, verify source scope, finite positive decimal, allowed memory unit, and a matching capacity in cited source text.
- [x] Add failing tests for foreign evidence, wrong supplier, altered field number, duplicate references, oversized sets and malformed structures; make each pass with targeted checks. An unencodable collection returns unavailable rather than a partial collection.
- [x] Run the focused tests and Ruff for the new files.

### Task 2: Integrate the codec with Run memory

**Files:** `backend/app/agent/session_memory.py`, `backend/tests/test_session_memory.py`

- [x] Add a failing test proving `serialize_session` writes `version=2`, `fact_sets_available=true` and fact sets, then `restore_session` recovers them in a new `AgentSession`. Add a version 1 historical-memory test and a tampered fact-set test expecting `memory_scope`.
- [x] Run `python.exe -m pytest backend/tests/test_session_memory.py -q`; observe the expected failures.
- [x] Call the codec from serialization and restoration. Restore result handles only after all facts pass validation; use a temporary decoded value so rejection leaves the target session untouched. Keep version 1 behavior and set no fact sets for it.
- [x] Run focused tests and Ruff.

### Task 3: Verify and record the slice

**Files:** `CLAUDE.md`, `README.md`, `docs/development-roadmap.md`, `docs/m3-11a-fact-persistence-2026-09-29.md`

- [x] Run `python.exe -m pytest backend/tests -q`, `python.exe -m ruff check backend evals`, `python.exe -m pip check` and `git diff --check`; record exact results.
- [x] Document that this is a durable fact basis only. Correction submission, new snapshot creation, immediate recheck, and deletion cleanup remain separate steps. No Docker or model call is required.
- [x] Review the staged diff and commit this self-contained milestone.
