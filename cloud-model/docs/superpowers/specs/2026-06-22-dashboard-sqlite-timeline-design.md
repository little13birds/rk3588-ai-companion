# Dashboard SQLite Timeline Design

## Goal

Unify parent-dashboard conversation, activity, mode-switch, parent speech, reading, and projected safety records behind a SQLite-backed event layer while keeping the current voice assistant and existing dashboard API behavior stable.

## Current Problems

- `DashboardState` keeps `_conversation` and `_activities` only in memory, so records disappear after restart.
- Conversation, activity, safety records, and reading captures use different storage formats and are merged late in view methods.
- System-level mode changes use plain `info`, so the frontend cannot style `story`, `reading`, and normal mode transitions distinctly.
- The homepage conversation and records activity views should both behave like social timelines: old items at the top, newest items at the bottom, and default scroll at the bottom.
- Parent-triggered speech and child/robot conversation need distinct labels.

## SQLite Event Model

Add `dashboard/event_store.py` with a small `DashboardEventStore` class using stdlib `sqlite3`.

Default database path:

```text
dashboard_records/dashboard.db
```

It can be overridden with:

```text
DASHBOARD_EVENT_DB=/path/to/dashboard.db
```

Schema:

```sql
CREATE TABLE IF NOT EXISTS dashboard_events (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  date_key TEXT NOT NULL,
  kind TEXT NOT NULL,
  level TEXT NOT NULL,
  actor TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_date_ts
  ON dashboard_events(date_key, ts, id);

CREATE INDEX IF NOT EXISTS idx_dashboard_events_kind_ts
  ON dashboard_events(kind, ts, id);
```

Query results are returned old-to-new. Events with the same second-level timestamp are ordered by SQLite insertion order (`rowid`), so conversation and activity rows do not jump around when several events are written in the same second.

Event fields:

- `kind`: `conversation`, `activity`, `system`, `safety`, `reading`, `parent_action`, `sleep`
- `level`: `info`, `success`, `warn`, `danger`
- `actor`: `child`, `robot`, `parent`, `system`, `safety`
- `title`: short display label
- `text`: human-readable message
- `meta`: JSON object for compatibility data, source, mode, event id, and old type

## Minimal Migration Strategy

This step only changes dashboard-side event persistence:

- `DashboardState.add_conversation()` writes `kind=conversation`.
- `DashboardState.add_activity()` writes `kind=activity`, or `kind=system` when recording mode changes.
- `DashboardState.queue_speech()` writes parent/sleep reminder conversation and activity events.
- `DashboardState.set_runtime()` writes mode-change system events.
- Existing `/api/conversation/summary` and `/api/activity` remain available and return compatible structures.
- New `/api/dashboard/timeline` and `/api/dashboard/events` return unified SQLite events.
- Safety records continue using `safety_records/index.jsonl`; `activity()` and `timeline()` project them into dashboard responses without moving image data.
- Reading captures continue using the current `reading_captures` files; `activity()` and `timeline()` project them into dashboard responses.

## Frontend Behavior

Homepage:

- Conversation is sorted old-to-new.
- Default scroll is bottom.
- Child, robot, and parent speech have distinct labels/classes.
- Parent broadcast/sleep reminder should be visibly different from normal robot replies.

Records page:

- "今日活动" becomes a timeline:
  - left: time
  - center: title and text
  - right: type badge
- Sort old-to-new.
- Default scroll is bottom.
- System events use a separate color from warning, reading, parent, and conversation events.

## Non-Goals

- Do not convert safety image directories into SQLite blobs.
- Do not replace `safety_records/index.jsonl` in this step.
- Do not add WebSocket/push updates.
- Do not restructure the single-file dashboard into a frontend build system.
