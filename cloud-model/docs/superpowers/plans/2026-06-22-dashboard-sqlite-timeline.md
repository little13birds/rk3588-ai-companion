# Dashboard SQLite Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move dashboard conversation/activity records to a SQLite event store and render old-to-new social-style timelines without breaking existing dashboard APIs.

**Architecture:** Add a small `DashboardEventStore` under `dashboard/` and let `DashboardState` write all new dashboard records through it. Keep old endpoints compatible, add unified timeline endpoints, and update the single-file frontend to consume the unified timeline for the records page while keeping homepage conversation behavior.

**Tech Stack:** Python stdlib `sqlite3`, existing `http.server` dashboard API, existing single-file HTML/CSS/JS dashboard, existing script-style Python tests.

---

### Task 1: SQLite Event Store

**Files:**
- Create: `dashboard/event_store.py`
- Create: `dashboard/test_event_store.py`

- [x] Write tests proving `DashboardEventStore` creates the schema, appends events, returns them old-to-new, filters by date/kind, and survives a new instance using the same DB path.
- [x] Run `python3 -m dashboard.test_event_store` and confirm it fails because `dashboard.event_store` does not exist.
- [x] Implement `DashboardEventStore` with WAL mode, indexes, JSON meta encoding, and a thread lock.
- [x] Re-run `python3 -m dashboard.test_event_store` and confirm it passes.

### Task 2: DashboardState Integration

**Files:**
- Modify: `dashboard/state.py`
- Modify: `dashboard/test_state.py`

- [x] Add failing tests proving `add_conversation()`, `add_activity()`, `set_runtime()`, and `queue_speech()` persist events to SQLite and can be read by a new `DashboardState` using the same DB.
- [x] Run `python3 -m dashboard.test_state` and confirm the new tests fail before implementation.
- [x] Modify `DashboardState` to own a `DashboardEventStore` and have current write methods append SQLite events.
- [x] Keep old response shapes for `conversation_summary()` and `activity()`.
- [x] Add `dashboard_events()` and `dashboard_timeline()` methods for unified event responses.
- [x] Re-run `python3 -m dashboard.test_state` and confirm it passes.

### Task 3: HTTP API Endpoints

**Files:**
- Modify: `dashboard/server.py`
- Modify: `dashboard/test_server_system.py`

- [x] Add failing tests for `GET /api/dashboard/events` and `GET /api/dashboard/timeline`.
- [x] Run `python3 -m dashboard.test_server_system` and confirm the new endpoint tests fail.
- [x] Add endpoints to `DashboardServer`.
- [x] Re-run `python3 -m dashboard.test_server_system` and confirm it passes.

### Task 4: Frontend Timeline Rendering

**Files:**
- Modify: `dashboard/parent-dashboard.html`
- Create: `scripts/test_dashboard_frontend_timeline.py`

- [x] Add failing static tests that check the frontend declares the timeline endpoint, uses a timeline renderer, scrolls activity to the bottom, and has CSS classes for system/parent/warn/read events.
- [x] Run `python3 -m scripts.test_dashboard_frontend_timeline` and confirm it fails.
- [x] Update `parent-dashboard.html` so homepage conversation and records activity both sort old-to-new and scroll to bottom.
- [x] Render records activity as a three-zone row: time, content, type badge.
- [x] Re-run `python3 -m scripts.test_dashboard_frontend_timeline` and confirm it passes.

### Task 5: Docs, Regression, Commit

**Files:**
- Modify: `.gitignore`
- Modify: `docs/superpowers/specs/2026-06-22-dashboard-sqlite-timeline-design.md`
- Modify: `docs/superpowers/plans/2026-06-22-dashboard-sqlite-timeline.md`

- [x] Add `dashboard_records/` to `.gitignore`.
- [x] Run all dashboard and previously relevant regression tests:

```bash
python3 -m dashboard.test_event_store
python3 -m dashboard.test_state
python3 -m dashboard.test_server_system
python3 -m scripts.test_dashboard_frontend_timeline
python3 -m scripts.test_runtime_status_logging
python3 -m scripts.test_logging_and_dashboard_speech
python3 -m scripts.test_tts_queue_accounting
python3 -m scripts.test_wake_flow
python3 -m scripts.test_interrupt_words
python3 -m scripts.test_wake_phrases
python3 -m scripts.test_start_system_script
python3 -m scripts.test_platform_camera_scripts
python3 -m py_compile dashboard/event_store.py dashboard/state.py dashboard/server.py main.py
git diff --check
```

- [ ] Commit all changes with `feat: persist dashboard timeline in sqlite`.
