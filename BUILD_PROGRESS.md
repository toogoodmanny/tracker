# Build Progress

Last updated: 2026-04-27

---

## Status Legend
- ✅ Complete + tested
- 🔨 In progress
- ⬜ Not started
- ❌ Blocked

---

## Phase 1 — Foundation

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Project scaffold + pyproject.toml | ✅ | |
| 2 | Virtualenv setup instructions | ✅ | setup.sh |
| 3 | DB schema (snapshots, sessions, goals, observations, corrections) | ✅ | |
| 4 | DB repository layer | ✅ | |
| 5 | DB migrations runner | ✅ | v2 — adds subgoals |
| 6 | Config loader + config.json | ✅ | |
| 7 | ActivityWatch client wrapper | ✅ | |
| 8 | Daemon loop (AW polling only) | ✅ | |
| 9 | `track start` CLI | ✅ | |
| 10 | `track end` CLI | ✅ | |
| 11 | `track sleep` CLI | ✅ | |
| 12 | `track break` CLI | ✅ | |
| 13 | `track plan` CLI | ✅ | |
| 14 | `track correct` CLI | ✅ | |
| 15 | `track note` CLI | ✅ | |
| 16 | Tests for all Phase 1 components | ✅ | |

## Phase 2 — Enrichment

| # | Task | Status | Notes |
|---|---|---|---|
| 17 | Text field sampler (pyobjc) | ✅ | macOS-only; Quartz frontmost detection, per-app AX query, Terminal/iTerm excluded |
| 18 | Chrome extension scaffold | ✅ | manifest v3 |
| 19 | WebSocket server in daemon | ✅ | thread-safe queue |
| 20 | YouTube title watcher (extension) | ✅ | MutationObserver on title |
| 21 | Page text sample (extension) | ✅ | 500 char sample |
| 22 | Doc word counter (.docx, .md) | ✅ | delta tracking |
| 23 | Screenshot taker + compressor | ✅ | screencapture + Pillow |
| 24 | Screenshot LLM trigger logic | ✅ | pure function, tested |
| 25 | PDF origin detector (quarantine xattr) | ✅ | |
| 25b | Reddit URL parsing (subreddit + post title) | ✅ | `_parse_reddit_url` in daily.py |

## Phase 3 — Intelligence

| # | Task | Status | Notes |
|---|---|---|---|
| 26 | Daily LLM analysis (Anthropic API) | ✅ | model: claude-sonnet-4-5-20250929 |
| 27 | HTML report renderer | ✅ | self-contained HTML, includes stats panel |
| 28 | Goal parsing from free text | ✅ | GoalParser |
| 29 | Goal vs reality comparison | ✅ | in report |
| 30 | Correction system | ✅ | track correct |
| 31 | Observations log (background filing) | ✅ | |
| 32 | `track end --no-analysis` | ✅ | |
| 32b | Screenshot vision analyser (Claude vision API) | ✅ | runs inside `track end` before daily report |
| 32c | Token tracking + capture stats panel | ✅ | input/output tokens, per-app text/screenshot counts |
| 32d | Robust JSON parsing + raw-response persistence | ✅ | recovers from truncation, fences, prose-wrapping |

## Phase 4 — Weekly & Self-improvement

| # | Task | Status | Notes |
|---|---|---|---|
| 33 | Weekly aggregation | ✅ | |
| 34 | Weekly LLM report | ✅ | |
| 35 | CLAUDE.md auto-update | ✅ | |
| 36 | Weekly interactive Q&A | ✅ | |
| 37 | System improvement suggestions | ✅ | suggest_category_updates |
| 38 | Category self-improvement suggestions | ✅ | patterns.py |

## Phase 5 — Polish

| # | Task | Status | Notes |
|---|---|---|---|
| 39 | `track status` mid-day summary | ✅ | rich terminal output |
| 40 | Pattern discovery (weekly, conservative) | ✅ | patterns.py, 3+ occurrence threshold |
| 41 | Video count + relevant/irrelevant | ✅ | in daily + weekly analysis |
| 42 | AI chat session audit (on-goal %) | ✅ | in daily + weekly analysis |
| 43 | Late session append logic | ✅ | LateSessionAppender |
| 44 | `track setup` first-run wizard | ✅ | first_run.py |
| 45 | CLAUDE.md bootstrap | ✅ | bootstrapped with user context |

## Phase 6 — Live Dashboard (NEW)

| # | Task | Status | Notes |
|---|---|---|---|
| 46 | Subgoals schema migration v2 | ✅ | new table + repo + model |
| 47 | Dashboard HTTP server (stdlib http.server) | ✅ | binds 127.0.0.1:27183 only |
| 48 | Dashboard frontend (single-file HTML+JS, 5s polling) | ✅ | tracker/dashboard/index.html |
| 49 | Editable goals + add/check/delete subgoals | ✅ | REST: /api/goals, /api/subgoals |
| 50 | Live timeline (latest 50 snapshots) | ✅ | shows app, title, URL, text capture, screenshot badges |
| 51 | `track dashboard` CLI command | ✅ | auto-opens browser |

---

## Test Coverage

| Module | Tests | Status |
|---|---|---|
| tracker/config.py | 14 tests | ✅ |
| tracker/db/ | 38 tests | ✅ |
| tracker/aw_client.py | 13 tests | ✅ |
| tracker/daemon.py | 10 tests | ✅ |
| tracker/cli/session_manager.py | 10 tests | ✅ |
| tracker/collectors/ | 32 tests | ✅ |
| tracker/analysis/daily.py | 26 tests | ✅ |
| tracker/analysis/screenshot_analyser.py | 6 tests | ✅ |
| tracker/analysis/patterns.py | 15 tests | ✅ |
| tracker/analysis/late_session.py + status | 13 tests | ✅ |
| tracker/dashboard/ | 3 tests | ✅ |
| **Total** | **180 tests** | **✅ all passing** |

---

## File Structure

```
tracker/
├── CODING_PRACTICES.md
├── PROJECT_PLAN.md
├── BUILD_PROGRESS.md
├── pyproject.toml
├── setup.sh
├── tracker/
│   ├── config.py
│   ├── daemon.py
│   ├── aw_client.py
│   ├── _daemon_runner.py
│   ├── core/
│   │   └── models.py            # +Subgoal
│   ├── db/
│   │   ├── schema.py            # v2 — adds subgoals
│   │   ├── connection.py
│   │   └── repositories.py      # +SubgoalRepository
│   ├── collectors/
│   │   ├── textfield.py         # Quartz frontmost + per-app AX
│   │   ├── docwatcher.py
│   │   ├── screenshot.py
│   │   ├── pdf_tracker.py
│   │   └── websocket_server.py
│   ├── analysis/
│   │   ├── daily.py                  # +token tracking, +Reddit parsing, +stats panel, +runs vision pipeline
│   │   ├── screenshot_analyser.py    # NEW — Claude vision pipeline
│   │   ├── weekly.py
│   │   ├── patterns.py
│   │   ├── late_session.py
│   │   └── prompts/
│   │       ├── daily-analysis.txt
│   │       ├── weekly-analysis.txt
│   │       ├── goal-parser.txt
│   │       └── screenshot-analysis.txt   # NEW
│   ├── dashboard/                    # NEW package
│   │   ├── server.py                 # stdlib http.server, REST endpoints
│   │   └── index.html                # polling frontend
│   └── cli/
│       ├── main.py                   # +`track dashboard`
│       ├── prompts.py
│       ├── output.py                 # stderr Console fix
│       ├── session_manager.py
│       ├── status.py
│       └── first_run.py
├── chrome_extension/
│   ├── manifest.json
│   ├── background.js
│   ├── content_youtube.js
│   └── content_pagetext.js
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_aw_client.py
    ├── test_daemon.py
    ├── db/test_repositories.py
    ├── db/test_subgoals.py            # NEW
    ├── cli/test_session_manager.py
    ├── collectors/test_collectors.py
    ├── dashboard/test_server.py       # NEW
    └── analysis/
        ├── test_daily.py
        ├── test_patterns.py
        ├── test_status_and_late_session.py
        └── test_screenshot_analyser.py  # NEW
```

---

## Daily flow (current)

1. `track start` → daemon polls AW + collectors every 30s, screenshots every 90s, queues unanalysed screenshots
2. (optional) `track dashboard` in another terminal → live view at http://127.0.0.1:27183
3. `track end` →
   a. screenshot vision pipeline runs on all pending screenshots (stores `screenshot_analysis` per row)
   b. daily LLM analysis runs (with Reddit parsing + AI chat audit + screenshot context)
   c. HTML report rendered with timeline, goals comparison, drift triggers, time distribution, **and run-stats panel** (tokens, capture counts)
   d. raw LLM response persisted to `~/.tracker/raw_responses/` so a parse failure doesn't waste the call

---

## Known Issues / Decisions

- pyobjc text field sampling: not testable in CI (Linux). Covered by conditional `_IS_MACOS` guard and unit tests for the non-macOS path.
- Claude Desktop text capture: unknown root cause — captured 0/106 in trial run while WhatsApp / Telegram / Word work. Diagnostic still pending.
- Screenshots: require Screen Recording permission on macOS Tahoe. screencapture returns exit code 1 silently if permission denied — caught and logged.
- Vision API call has been unit-tested with mocks but NOT yet smoke-tested end-to-end against a real JPEG. First real `track end` after this change will validate the payload format.
- Dashboard binds 127.0.0.1 only — never network-exposed. Each request opens its own SQLite handle (fine for single user, would not scale).
- ActivityWatch: must be running before `track start`. First-run wizard checks and warns.
- Electron apps (WhatsApp, Figma): window titles reliable; AX text field patchy. Screenshot fallback + vision pipeline now cover the gap.
- Late session: currently produces heuristic summary only. `track end --full` flag available for full LLM analysis.
