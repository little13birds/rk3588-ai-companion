# Dashboard V3 Frontend Notes

Date: 2026-06-30
Branch: `master`

## Purpose

Dashboard V3 is the default parent-facing dashboard frontend for real-device review. The legacy single-file dashboard is deprecated and kept only as a rollback entry.

- Default route: `http://<board-ip>:8080/`
- V3 compatibility route: `http://<board-ip>:8080/v3-dashboard/`
- Deprecated legacy route: `http://<board-ip>:8080/legacy-dashboard/`
- Legacy file: `dashboard/parent-dashboard.html`
- V3 static root: `dashboard/v3_static/`

`/parent-dashboard.html` is retained for old bookmarks, but responses are marked with `X-Dashboard-Deprecated: legacy`.

## File Layout

- `dashboard/v3_static/index.html`
  - Page shell, modals, control panels, history gallery, and system information panels.
- `dashboard/v3_static/css/base.css`
  - Theme variables, reset, typography, and day/night theme tokens.
- `dashboard/v3_static/css/layout.css`
  - App frame, sidebar, top bar, page grid, responsive drawer layout.
- `dashboard/v3_static/css/components.css`
  - Cards, camera panels, drive controls, modals, history gallery, toast, filters.
- `dashboard/v3_static/js/api.js`
  - Thin HTTP client for V3 and shared dashboard APIs.
- `dashboard/v3_static/js/state.js`
  - Browser-side state, date helpers, page metadata, and history category labels.
- `dashboard/v3_static/js/render.js`
  - DOM rendering for status, conversation, sleep recipients, history, gallery, and system components.
- `dashboard/v3_static/js/app.js`
  - Event binding, page switching, polling, forms, drive control repeat, and modal workflows.

Backend compatibility is implemented in:

- `dashboard/server.py`
  - Serves `/` as the V3 dashboard, keeps `/v3-dashboard/` as a compatibility route, and marks legacy routes as deprecated.
  - Adds the V3 compatibility API routes.
- `dashboard/state.py`
  - Builds V3 response shapes from existing runtime state and SQLite-backed settings/history.
- `dashboard/test_server_v3_dashboard.py`
  - Lightweight compatibility tests for static serving, API shape, drive-repeat JS, history grid CSS, and filter layout.

## API Contract

V3 uses these compatibility endpoints:

- `GET /api/config`
  - Returns UI refresh intervals, theme, sleep reminder text, sleep time, and selected children.
- `POST /api/config`
  - Persists V3 UI settings through `DashboardEventStore`.
  - Clamps refresh intervals and history page size to safe ranges.
- `GET /api/status`
  - Returns camera source, environment summary, child visibility, safety summary, and sleep aid state.
- `GET /api/system/components`
  - Returns a compact list of system components for the System page.
- `GET /api/history?date=YYYY-MM-DD`
  - Returns one date of categorized history for the History page.
- `GET /api/history/gallery?category=safety,reading&from=YYYY-MM-DD&to=YYYY-MM-DD`
  - Returns gallery rows with checkbox-selected categories and a date range.

V3 also reuses existing dashboard endpoints:

- `POST /api/message/send`
- `POST /api/move`
- `POST /api/move/emergency-stop`
- `POST /api/person-task/seek`
- `POST /api/person-task/stop`
- `POST /api/sleep/remind`
- `POST /api/sleep/aid/start`
- `POST /api/sleep/aid/stop`
- `GET /api/camera/snapshot`
- `GET /api/camera/history/image/<path>`

## Interaction Rules

### Navigation

The active page is stored in browser local storage and mirrored in the hash. Refreshing V3 should keep the current subpage instead of returning to the home page.

### Camera

The live camera frame uses `/api/camera/snapshot` and the backend marks the source through `X-Camera-Source`.

- Normal mode: platform camera.
- Reading mode: reading-arm camera when available.

The V3 frontend should label the camera source clearly because the two cameras have different purposes and failure modes.
The V3 frontend fetches snapshots as blobs with an in-flight guard. It does not start a new image request until the previous one finishes.

### Chassis Controls

Direction buttons must behave like the legacy dashboard:

- Send one move command immediately on pointer down.
- Repeat the same move intent while pressed.
- Send `stop` on pointer up, pointer leave, cancel, lost pointer capture, window blur, or page hidden.
- Do not queue unbounded HTTP move requests. The frontend keeps at most one in-flight move request and one pending latest direction.
- While a manual move is active, camera frame refresh is skipped so a slow Wi-Fi link does not starve movement commands.

The production backend must keep using the established safe chassis adapter. Do not directly publish raw chassis velocity from the dashboard.

### History

The History page defaults to today's records. Items are grouped by category on the date view and by date in the full gallery.

Gallery rules:

- Newer items sort first.
- Date range is clamped to the last 30 days.
- Content type selection is a vertical dropdown checkbox list.
- Image previews use a responsive grid and large image tiles for review.

### Sleep Settings

Sleep reminder text and selected children are persisted through `/api/config`. The current V3 UI is a parent-control surface; the actual sleep-presence trigger still depends on the running backend monitor and the face identity service.

## Current Status

Implemented and committed:

- V3 default route and static serving.
- Deprecated legacy rollback route.
- V3 compatibility APIs.
- Day/night theme toggle.
- Status, camera, system, history, sleep, and control pages.
- Sleep reminder text persistence.
- Sleep child selection UI.
- Sleep aid modal.
- Toast notifications for failure and success feedback.
- Long-press drive repeat with stop-on-release behavior.
- Slow-link protection for dashboard image refresh and movement commands.
- Responsive history image grid.
- Full gallery with date range and dropdown checkbox filters.

Still requiring board-side review:

- All parent controls on real chassis hardware.
- Long-press drive behavior under poor network conditions.
- Full gallery usability with many real images.
- Person enrollment and deletion parity with the legacy/standalone tools.
- Rich seek/follow status modal parity with the dedicated tracking UI.
- Reading-corner overlay preview parity with the legacy dashboard.

## Verification

Run from `/home/elf/cloud-model-safety-mainline`:

```bash
python3 -m dashboard.test_server_v3_dashboard
python3 -m dashboard.test_server_static
python3 -m dashboard.test_server_system
node dashboard/test_client_state.js
for f in dashboard/v3_static/js/*.js; do node --input-type=module --check < "$f" >/dev/null || exit 1; done
```

Manual board check:

```bash
./scripts/start_system.sh
```

Then open:

```text
http://<board-ip>:8080/v3-dashboard/
```

Check at minimum:

- Home camera updates.
- Control page camera updates.
- Long-press movement repeats while pressed and stops after release.
- On a poor Wi-Fi link, movement commands should not pile up; image refresh should pause while a direction button is held.
- Emergency stop sends successfully.
- History defaults to today.
- Full gallery opens and filters by checkbox categories.
- Sleep settings save and persist after refresh.
- Default route `/` opens V3.
- Legacy dashboard still opens at `/legacy-dashboard/` and sends `X-Dashboard-Deprecated: legacy`.

## Rollback

If V3 has a UI issue during testing, use the deprecated legacy dashboard at `/legacy-dashboard/` without changing the backend.

To make legacy the default again, change the `/` route in `dashboard/server.py` back to `dashboard/parent-dashboard.html`. Do not delete `dashboard/parent-dashboard.html` unless the rollback window is intentionally closed.

## Performance Note

On 2026-07-03, a dashboard latency investigation showed that the backend was not the bottleneck:

- Board-local `/api/camera/snapshot` returned a ~59 KB JPEG in roughly 4-11 ms.
- Remote browser-side access over Wi-Fi took roughly 4-10 s for the same snapshot.
- A temporary 512 KB HTTP download from the board to the host took 35.2 s, about 14.9 KB/s.
- Ping from host to board averaged about 461 ms with 5% loss; ping from board to gateway averaged about 508 ms.
- The board had both `wlP4p65s0` station mode and `ax200ap` hotspot mode active on the same Wi-Fi device, which can severely hurt latency and throughput.

If live video or movement control becomes choppy again, check Wi-Fi/hotspot state before changing dashboard code.
