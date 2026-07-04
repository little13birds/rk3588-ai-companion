# Dashboard People Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dashboard-facing person database workflow for listing, enrolling, deleting, selecting sleep children, and starting/stopping person seek tasks.

**Architecture:** Cloud-model dashboard owns a small proxy layer that talks to the board person tracker service. The frontend never receives raw embeddings; upload/capture results become short-lived backend candidates, and enrollment sends only `candidate_id + unique_name`. Person seek controls use the existing `person_tasks` controller path and keep `/api/move/find-child` as a compatibility wrapper.

**Tech Stack:** Python stdlib HTTP server, dashboard state classes, existing `face_identity_rk3588` person tracker HTTP API, existing single-file dashboard HTML/JS, pytest.

---

### Task 1: Person Registry Backend

**Files:**
- Create: `dashboard/people.py`
- Modify: `dashboard/state.py`
- Modify: `dashboard/server.py`
- Test: `dashboard/test_people.py`
- Test: `dashboard/test_server_people.py`

- [ ] Write tests for listing people through an injected fake person client.
- [ ] Implement `PersonIdentityClient` with `list_people`, `delete_person`, `capture_candidates_from_jpeg`, and `enroll_candidate` interfaces.
- [ ] Add `DashboardState.people_registry()` and wire `GET /api/people`.
- [ ] Run focused pytest for people backend.

### Task 2: Person Management Page

**Files:**
- Modify: `dashboard/parent-dashboard.html`
- Test: `scripts/test_dashboard_people_page.py`

- [ ] Add a `people` page/tab and render `GET /api/people` results.
- [ ] Add delete buttons using `POST /api/people/delete`.
- [ ] Add upload and platform-camera capture controls.
- [ ] Render unknown candidates with face crop, name input, and enroll button.
- [ ] Run HTML smoke tests.

### Task 3: Enrollment APIs

**Files:**
- Modify: `dashboard/server.py`
- Modify: `dashboard/state.py`
- Modify: `dashboard/people.py`
- Test: `dashboard/test_people.py`
- Test: `dashboard/test_server_people.py`

- [ ] Add `POST /api/people/candidates/upload` for browser image data.
- [ ] Add `POST /api/people/candidates/capture` for platform camera snapshot.
- [ ] Add `POST /api/people/enroll` for `candidate_id + unique_name`.
- [ ] Keep embeddings in a short-lived server cache.
- [ ] Run focused pytest.

### Task 4: Sleep Children Checkboxes

**Files:**
- Modify: `dashboard/parent-dashboard.html`
- Test: `scripts/test_dashboard_people_page.py`

- [ ] Replace the primary sleep child text input with people checkboxes populated from `/api/people`.
- [ ] Preserve existing `/api/sleep/settings` payload shape: `children: [unique_name]`.
- [ ] Keep a fallback manual text path only if people service is unavailable.

### Task 5: Find Child Modal and Timeout

**Files:**
- Modify: `dashboard/state.py`
- Modify: `dashboard/server.py`
- Modify: `dashboard/parent-dashboard.html`
- Test: `dashboard/test_server_people.py`
- Test: `scripts/test_dashboard_people_page.py`

- [ ] Add `POST /api/person-task/seek`, `POST /api/person-task/stop`, and `GET /api/person-task/status`.
- [ ] Add a 60-second backend timeout for dashboard-started seek tasks.
- [ ] Change the control page "找孩子" button to open a remembered person selection modal.
- [ ] Show running status, elapsed/remaining time, and stop button.
- [ ] Keep `/api/move/find-child` working by forwarding to the new seek path.

### Task 6: Documentation and Verification

**Files:**
- Modify: `docs/SAFETY_GUARD_MAINLINE_STATUS.md` if present, otherwise create/update a dashboard status document.

- [ ] Document new dashboard APIs and test commands.
- [ ] Upload changed files to the board.
- [ ] Run focused pytest on the board.
- [ ] Commit on `master` after the safety stack merge.

### Self-Review

- The plan covers person listing, delete, upload/capture enrollment, child checkbox settings, find-child modal, timeout, and docs.
- No raw embeddings are sent to the browser.
- Existing APIs remain compatible.
- ROS code is not changed by this plan.
