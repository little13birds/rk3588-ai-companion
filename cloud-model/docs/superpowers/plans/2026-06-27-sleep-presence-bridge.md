# Sleep Presence Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Connect platform-camera person identity results to dashboard sleep presence without changing ROS or adding reminder/snapshot side effects.

**Architecture:** Add a testable `DashboardState.refresh_sleep_presence_from_identity()` method that uses the existing platform snapshot provider and `PersonIdentityClient`. Start a small background worker in `main.py` after safety guard and camera providers are initialized.

**Tech Stack:** Python dashboard state, existing person identity HTTP client, main loop background thread, static tests.

---

### Task 1: Dashboard State Bridge

**Files:**
- Modify: `dashboard/state.py`
- Test: `dashboard/test_people.py`

- [x] Add a failing test that configures sleep children, injects a platform JPEG provider, injects a fake person client returning known faces, and expects only configured known children to become visible.
- [x] Implement `refresh_sleep_presence_from_identity()` with mode/source guards.
- [x] Keep absence handling TTL-based by not writing `visible=false` for missed frames.
- [x] Run `python3 -m dashboard.test_people`.

### Task 2: Main Worker

**Files:**
- Modify: `main.py`
- Test: `scripts/test_person_task_main_static.py`

- [x] Add a failing static test requiring `DASHBOARD_SLEEP_PRESENCE_ENABLED`, `DASHBOARD_SLEEP_PRESENCE_INTERVAL_SEC`, a stop event, and a `sleep-presence` thread.
- [x] Start the worker after `dashboard_state.set_camera_snapshot_provider(safety_guard.camera_snapshot)`.
- [x] Stop and join the worker during shutdown.
- [x] Run `python3 -m scripts.test_person_task_main_static`.

### Task 3: Docs And Verification

**Files:**
- Modify: `docs/DASHBOARD_PEOPLE_MANAGEMENT.md`

- [x] Document worker behavior and environment switches.
- [x] Keep reminder throttling and sleep snapshots as follow-up work.
- [x] Run focused local tests before board sync.
