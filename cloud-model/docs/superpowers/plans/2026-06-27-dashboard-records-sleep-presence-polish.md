# Dashboard Records And Sleep Presence Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve dashboard records/care usability and expose sleep-presence debugging without touching ROS, hardware control, or automatic person-recognition loops.

**Architecture:** Keep the first implementation batch inside `dashboard/parent-dashboard.html`, `dashboard/state.py`, static tests, and docs. Reuse existing `/api/camera/history`, `/api/safety/status`, and `/api/sleep/status` data; only extend sleep status with debug metadata derived from existing in-memory presence timestamps.

**Tech Stack:** Single-file dashboard HTML/JS, Python state object, static Python tests, existing dashboard test suite.

---

### Task 1: Reading Records List

**Files:**
- Modify: `scripts/test_dashboard_frontend_timeline.py`
- Modify: `dashboard/parent-dashboard.html`

- [x] Add a failing static test requiring `reading-record-list`, `reading-record-row`, `sortReadingRecords`, and no per-page book icon rendering in `updateGallery()`.
- [x] Replace the icon grid with a compact scrollable list, sorted old-to-new so newest rows appear at the bottom.
- [x] Run `python3 -m scripts.test_dashboard_frontend_timeline`.

### Task 2: History Categories

**Files:**
- Modify: `scripts/test_dashboard_frontend_timeline.py`
- Modify: `dashboard/parent-dashboard.html`

- [x] Add a failing static test requiring `history-category-row`, `historyCategory`, and category labels for safety, reading, and other snapshots.
- [x] Render `/api/camera/history?date=` snapshots grouped into category rows, refreshing on date changes.
- [x] Keep existing image preview and book-corner overlay behavior.
- [x] Run `python3 -m scripts.test_dashboard_frontend_timeline`.

### Task 3: Care Safety Event Row

**Files:**
- Modify: `scripts/test_dashboard_control_page.py`
- Modify: `dashboard/parent-dashboard.html`

- [x] Add a failing static test requiring `care-event-list`, `updateCareEvents`, and history image links from care events.
- [x] Add a care-page event list below safety cards showing recent safety snapshots for today.
- [x] Call `updateCareEvents()` with the care refresh loop.
- [x] Run `python3 -m scripts.test_dashboard_control_page`.

### Task 4: Sleep Presence Debugging

**Files:**
- Modify: `dashboard/test_state.py`
- Modify: `scripts/test_dashboard_control_page.py`
- Modify: `dashboard/state.py`
- Modify: `dashboard/parent-dashboard.html`

- [x] Add a failing state test requiring `/api/sleep/status` data to include configured children, visible children, presence TTL, and last-seen age.
- [x] Add a failing static frontend test requiring `sleep-presence-debug` and `renderSleepPresenceDebug()`.
- [x] Extend `DashboardState.sleep_status()` with a `presence` object derived from existing `_sleep_presence` timestamps.
- [x] Render sleep presence debug text in the care page.
- [x] Run `python3 -m dashboard.test_state` and `python3 -m scripts.test_dashboard_control_page`.

### Task 5: Docs, TODO, Verification, Board Sync

**Files:**
- Modify: `docs/DASHBOARD_PEOPLE_MANAGEMENT.md`
- Modify: `.codex-local-todo.md` if present in the local safety workspace, otherwise document follow-ups in `docs/DASHBOARD_PEOPLE_MANAGEMENT.md`.

- [x] Document completed UI/presence debugging changes.
- [x] Add follow-ups for automatic sleep-child identity polling, sleep reminder throttling, and sleep-presence snapshots with cooldown.
- [x] Run focused local dashboard tests.
- [x] Sync changed files to `/home/elf/cloud-model-safety-mainline`.
- [x] Run focused board tests.
- [x] Commit on board if tests pass.
