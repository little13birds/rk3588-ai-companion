# Dashboard People Management

## Scope

The parent dashboard now treats the person registry as a shared project resource. It is used by:

- person enrollment and deletion;
- sleep child selection;
- dashboard-triggered person seek;
- future safety-event attribution.

The dashboard proxies the board person tracker service instead of exposing tracker endpoints directly to the browser.

## Backend APIs

Dashboard APIs:

```text
GET  /api/people
POST /api/people/candidates/upload
POST /api/people/candidates/capture
POST /api/people/enroll
POST /api/people/delete
POST /api/person-task/seek
POST /api/person-task/stop
GET  /api/person-task/status
```

Compatibility:

```text
POST /api/move/find-child
```

This still works and forwards to the new person seek path when the person task controller is connected.

## Enrollment Flow

1. Browser upload or platform camera capture provides one image.
2. Dashboard calls the person tracker observe API with face embedding and crop enabled.
3. Known faces above the confidence threshold are returned as `known_faces` and are not enrollable.
4. Unknown faces are stored in a short-lived server-side candidate cache.
5. Browser receives `candidate_id`, face crop, quality, bbox, and source. Raw embeddings are not sent to the browser.
6. User enters `unique_name`, then `/api/people/enroll` sends the cached embedding to the tracker service.

Candidate cache TTL is five minutes.

## Frontend

The dashboard has a new `人物` tab:

- list registered people;
- delete people;
- upload an image for enrollment;
- capture from the platform camera for enrollment;
- name each unknown face from its crop.

The `看护` sleep settings now render person checkboxes from `/api/people`. The old comma-separated input remains as a fallback when the person service is unavailable.

The `控制` page `找孩子` button opens a target selection modal. The modal remembers the previous selection in browser local storage and starts a 60-second seek task. It also provides a stop button.

## 2026-06-27 Safe Dashboard Polish

This batch intentionally stays inside the dashboard frontend and static tests. It does not change ROS, hardware control, safety inference, or the main voice runtime.

Changes:

- Live, care, and control camera panels now display the current camera source from `/api/camera/source`, so users can see whether the stream is using the platform camera or reading-arm camera.
- The records page no longer labels historical snapshots as a reserved feature. The empty state now describes safety-event screenshots, reading screenshots, and book-page corner overlays.
- The `找孩子` modal now loads the people list and sleep-child settings before rendering choices. If sleep children are configured, they are shown first as the seek targets.
- Starting a find task is guarded against duplicate button clicks while the request is in flight.

## 2026-06-27 Records And Sleep Presence Polish

This batch still avoids ROS, hardware control, automatic identity polling, and safety inference changes.

Changes:

- Reading records now render as a compact scrollable list instead of one icon per page. The list is sorted old-to-new so the newest rows stay near the bottom.
- Camera history now groups snapshots by category rows: safety events, reading screenshots, and other images. Date switching reloads all category rows.
- The care page now shows recent safety-event image entries for the current day and opens the corresponding history image.
- `/api/sleep/status` now includes a `presence` debug object with configured children, currently visible children, a presence TTL, and per-child last-seen age.
- The care page renders the sleep presence debug state every three seconds so it is clear whether the dashboard has received `/api/sleep/presence` updates.
- Dashboard camera snapshot client disconnects are logged as `client_disconnected` instead of traceback errors; browser refreshes or aborted image requests are expected during live preview.

## 2026-06-27 Automatic Sleep Presence Bridge

The main process now starts a lightweight sleep-presence worker after the platform camera snapshot provider is connected.

Behavior:

- Enabled by default with `DASHBOARD_SLEEP_PRESENCE_ENABLED=1`.
- Poll interval defaults to three seconds and can be adjusted with `DASHBOARD_SLEEP_PRESENCE_INTERVAL_SEC`.
- The worker only reports presence when dashboard sleep settings contain configured children and the runtime mode is `normal`.
- It sends the current platform snapshot through the existing person identity observe path and marks matched configured children as visible in dashboard sleep state.
- Missing detections are not immediately written as invisible. The existing 30-second presence TTL handles expiry, reducing flicker from one missed frame.

This bridge does not add sleep reminders or sleep-time snapshots yet.

## Tests

Run from the cloud-model root:

```bash
PYTHONPATH=. python3 dashboard/test_people.py
PYTHONPATH=. python3 dashboard/test_server_people.py
PYTHONPATH=. python3 scripts/test_dashboard_people_page.py
PYTHONPATH=. python3 scripts/test_dashboard_person_wiring.py
PYTHONPATH=. python3 scripts/test_dashboard_control_page.py
PYTHONPATH=. python3 -m py_compile dashboard/people.py dashboard/state.py dashboard/server.py main.py
```

## Current TODO

Manual validation checklist:

- Open live, care, and control pages and confirm each camera panel shows a `画面源` label.
- Open the records page with no matching images and confirm the history empty state does not say `预留`.
- Open the records page with reading data and confirm reading records use compact rows rather than one page icon per page.
- Open the records page history area and confirm safety/reading/other snapshots are grouped into separate rows.
- Open the care page and confirm recent safety event images appear below the safety cards when same-day safety records exist.
- Open the care page sleep area and confirm presence debug text shows configured children, visible children, TTL, and last-seen state.
- Configure one or more sleep children, then open `控制 -> 找孩子` and confirm those children are preferred in the target list.
- Double-click the find confirmation button and confirm only one seek task is started.
- Open the dashboard and confirm the `人物` tab loads `/api/people`.
- Upload a local image that contains one known face and one unknown face; confirm known faces are excluded and unknown faces become enrollment candidates.
- Enroll an unknown face from browser upload and confirm it appears in the person list.
- Use platform camera capture enrollment and confirm the crop/name workflow is the same as upload.
- Delete a test person and confirm `/api/people` refreshes without that person.
- In the `看护` page, select sleep children from person checkboxes and confirm `/api/sleep/settings` stores `children` as `unique_name` values.
- In the `控制` page, open `找孩子`, confirm the last selected target is remembered, start seek, stop seek, and verify status text updates.
- Let a dashboard-started seek run without stopping and confirm the 60-second timeout stops it.
- Confirm seek arrival updates dashboard status instead of leaving the UI in a running state.

Follow-up implementation candidates:

- Add a safe rename/edit endpoint after the tracker service exposes a dedicated rename operation.
- Persist dashboard find-child default target on the backend if browser-local memory is not enough.
- Add sleep reminder throttling based on `remind_interval_min` after automatic presence updates are connected.
- Add sleep-time child presence snapshots with a per-child cooldown after automatic presence updates are connected.
- Attach recognized `unique_name` to safety events when a person can be associated with the event frame.
- Add depth-aware ordering for `observe_people_identity` so "front/near/far" is available to the LLM and dashboard.
- Add a compact person-management smoke test that can run against the real board tracker service.

## Notes

- The board person tracker service is expected at `PERSON_TRACKER_URL`, defaulting to `http://127.0.0.1:8102`.
- Actual face recognition quality depends on the tracker service and identity database in `face_identity_rk3588`.
- Rename/edit is intentionally not implemented yet because the tracker API exposes safe delete/enroll paths but no dedicated rename endpoint.
