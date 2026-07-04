# Dashboard Safe Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the parent dashboard's already-connected controls without changing ROS, main runtime, or hardware control behavior.

**Architecture:** Keep all changes inside the dashboard HTML/JS and dashboard static tests. Reuse existing APIs instead of adding new server behavior: `/api/camera/source`, `/api/people`, `/api/sleep/children`, `/api/person-task/status`, and existing camera/history endpoints.

**Tech Stack:** Python static tests, single-file dashboard HTML/JS, existing `dashboard/client_state.js` refresh policy.

---

### Task 1: Camera Source Visibility

**Files:**
- Modify: `dashboard/parent-dashboard.html`
- Test: `scripts/test_dashboard_control_page.py`

- [x] Add a failing static test that requires the frontend to declare `/api/camera/source`, render camera source badges for live/care/control views, and call `refreshCameraSource()`.
- [x] Implement source labels in all camera footers.
- [x] Add `cameraSource` to `ENDPOINTS`.
- [x] Implement `refreshCameraSource()` and schedule it with each camera page refresh.
- [x] Run `python3 -m scripts.test_dashboard_control_page`.

### Task 2: History Page Wording

**Files:**
- Modify: `dashboard/parent-dashboard.html`
- Test: `scripts/test_dashboard_frontend_timeline.py`

- [x] Add a failing static test that rejects the stale title `历史画面（预留）`.
- [x] Rename the section to real feature wording and update empty-state copy to mention safety/reading snapshots.
- [x] Run `python3 -m scripts.test_dashboard_frontend_timeline`.

### Task 3: Find Child Modal Flow

**Files:**
- Modify: `dashboard/parent-dashboard.html`
- Test: `scripts/test_dashboard_people_page.py`

- [x] Add failing static tests for async modal loading, child-priority target rendering, and a running state flag that disables duplicate starts.
- [x] Make `openFindChildModal()` async and await `loadPeople()`.
- [x] Cache `/api/sleep/children` data and prefer configured children when rendering find targets.
- [x] Add `personTaskStarting` to prevent double starts and improve status text.
- [x] Run `python3 -m scripts.test_dashboard_people_page`.

### Task 4: Verification and Docs

**Files:**
- Modify: `docs/DASHBOARD_PEOPLE_MANAGEMENT.md`

- [x] Document this safe-polish batch and remaining follow-ups.
- [x] Run focused dashboard tests locally where possible.
- [x] Upload to the board.
- [x] Run focused dashboard tests on the board.
- [x] Historical note: this work originally landed on `feat/safety-guard-mainline`; current integration target is `master`.
