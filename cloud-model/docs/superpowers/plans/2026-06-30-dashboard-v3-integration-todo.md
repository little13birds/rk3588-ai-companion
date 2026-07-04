# Dashboard V3 Integration TODO

Date: 2026-06-30

## Current Safe Integration

- The new dashboard frontend is integrated as an isolated route: `/v3-dashboard/`.
- The legacy dashboard remains available at `/` and `/parent-dashboard.html`.
- Static assets are served from `dashboard/v3_static/`.
- Long-term maintenance notes are in `docs/DASHBOARD_V3_FRONTEND.md`.
- 2026-06-30 follow-up fixes:
  - V3 chassis direction buttons now repeat move intent while pressed and send stop on release/cancel/blur.
  - History homepage and full gallery now use responsive image grids instead of horizontal thumbnail strips.
  - Full gallery content-type filter is a vertical dropdown checkbox list, not a 2x2 grid.
- Compatibility API endpoints were added for the V3 frontend:
  - `GET /api/config`
  - `POST /api/config`
  - `GET /api/status`
  - `GET /api/system/components`
  - `GET /api/history`
  - `GET /api/history/gallery`

## Why This Is Isolated

The V3 frontend is visually cleaner but still needs real-device review. Keeping it under
`/v3-dashboard/` lets the team test it without risking the legacy parent dashboard during
competition preparation.

## Next Test Checklist

- Open `http://<board-ip>:8080/v3-dashboard/`.
- Verify the live camera appears on home and control pages.
- Verify refreshing V3 preserves the current subpage through URL hash/local storage.
- Verify long-press chassis movement sends repeated move intents and stops when released.
- Verify the history gallery opens, defaults to today's records, and the content-type filter
  uses the dropdown checkbox UI.
- Verify multi-select history filtering with at least:
  - dangerous event + reading record
  - dangerous event only
  - reading record only
- Verify the bottom toast appears when no content type is selected.
- Verify parent broadcast, emergency stop, stop follow/seek, sleep reminder, and sleep settings
  do not break the main process.

## Follow-Up After User Test

- If V3 is accepted, decide whether to promote `/v3-dashboard/` to the default `/` route.
- Port any missing legacy functions that are still needed before promotion, especially:
  - people enrollment and deletion UI
  - rich person seek/follow status modal
  - reading-corner overlay preview parity with the legacy dashboard
- Keep legacy dashboard available until V3 has passed a full board-side test session.
