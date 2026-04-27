# Tracker — Help context for Claude

Paste this entire file at the start of a Claude conversation, then describe your problem in your own words below it. Claude will use this context to walk you through the fix.

---

## What is this tool?

This is a personal time-tracking CLI for macOS called "tracker". It runs on your Mac, captures what you're doing every 30 seconds, and at the end of the day calls Claude (Anthropic's API) to write a personalized productivity report.

The user is talking to you because something isn't working. Your job is to diagnose the issue based on the user's description and the context below, then walk them through fixing it. Be patient — they may not be technical.

## Where things live on the user's Mac

- **Code:** `~/.local/share/tracker/` (full source — read this if you need to inspect behaviour)
- **Config:** `~/.tracker/config.json` (their answers from `track setup`)
- **Personal context:** `~/.tracker/CLAUDE.md` (their tone preferences, projects, etc.)
- **Database:** `~/.tracker/tracker.db` (SQLite — all snapshots, sessions, goals)
- **Reports:** `~/.tracker/reports/YYYY-MM-DD.html` (one per day)
- **Raw LLM responses:** `~/.tracker/raw_responses/` (saved before parsing in case parse fails)
- **Screenshots:** `~/.tracker/screenshots/` (compressed JPEGs)
- **Active session state:** `~/.tracker/session.json`
- **Chrome extension folder:** `~/.local/share/tracker/chrome_extension/`
- **Tracker binary:** `/usr/local/bin/track` or `~/.local/bin/track` (a shim into the venv)
- **Venv with all deps:** `~/.local/share/tracker/.venv/`

## The CLI commands

| Command | Purpose |
|---|---|
| `track setup` | First-time wizard — writes config.json + CLAUDE.md |
| `track plan` | Set goals for tomorrow (or today) |
| `track start` | Begin tracking — launches a background daemon |
| `track status` | Quick session summary |
| `track dashboard` | Live web UI at http://127.0.0.1:27183 |
| `track break N` | Mark an N-minute intentional break |
| `track correct "..."` | Record a misclassification |
| `track note "..."` | Add a freeform note |
| `track sleep` | Pause until tomorrow |
| `track end` | Stop, run vision pipeline on flagged screenshots, generate daily HTML report |
| `track week` | Generate weekly report (run on Sunday) |

## Architecture (high level)

1. **Daemon** (`tracker/daemon.py`) — every 30s polls ActivityWatch (window+AFK), every 90s captures a screenshot if conditions trigger, every poll asks the textfield collector for the focused text field's value. All writes go to SQLite via the repository layer.
2. **Collectors** (`tracker/collectors/`) — `textfield.py` (macOS Accessibility API), `screenshot.py`, `docwatcher.py` (.docx/.md word counts), `pdf_tracker.py` (quarantine xattr), `websocket_server.py` (port 27182, takes events from the Chrome extension).
3. **Analysis** (`tracker/analysis/`) — `daily.py` orchestrates the daily flow: runs `screenshot_analyser.py` first (vision LLM), then sends the day's data + goals + corrections to Claude, parses the JSON response, renders an HTML report.
4. **Dashboard** (`tracker/dashboard/server.py`) — stdlib `http.server` on 127.0.0.1:27183, single-page HTML frontend polling `/api/state` every 30s.
5. **Database** (`tracker/db/`) — `schema.py` has all DDL, `repositories.py` has all queries (no raw SQL anywhere else), `connection.py` runs migrations on open.

## External dependencies (separate installs)

- **ActivityWatch** desktop app on http://localhost:5600 — must be running
- **Anthropic API key** in config.json — required for daily reports
- **Chrome extension** loaded in dev mode — optional, adds YouTube + page content
- **macOS permissions** granted to the Terminal app the user actually uses — required for textfield + screenshot

## Common failure modes

### "command not found: track"
- The shim isn't on PATH. Check `/usr/local/bin/track` or `~/.local/bin/track` exists.
- If it exists but PATH is missing the dir, add it to `~/.zshrc`.
- If the shim is missing, re-run the installer: `curl -fsSL <REPO>/install.sh | bash`.

### "ActivityWatch is not running" (`track start` warning)
- Open the ActivityWatch app from Applications. Look for the menu bar icon.
- If it's running but tracker can't reach it, check `curl http://localhost:5600/api/0/info` — should return JSON.

### `Anthropic API error: invalid api key`
- `~/.tracker/config.json` → `anthropic_api_key` field is empty or wrong.
- The user can paste a fresh key from https://console.anthropic.com/api-keys.

### `Anthropic API error: model not found`
- The model name in config.json is retired. Update to the latest sonnet model.

### `Daily analysis failed: invalid JSON / missing required keys`
- The raw response is saved at `~/.tracker/raw_responses/daily-<date>-<time>.txt`. Read it. Often either truncated (max_tokens hit — bump the value in `tracker/analysis/daily.py`) or a model formatting issue (the raw response will show prose around the JSON, which the parser usually handles but might not in edge cases).

### "Screen Recording permission not granted" / "Accessibility permission not granted"
- The user granted permission to one Terminal-like app (e.g. iTerm) but is running tracker from a different one (e.g. Terminal.app). They need to add **whichever app they're typing `track` into** to System Settings → Privacy & Security → both Accessibility and Screen Recording. After granting, **fully quit Terminal with Cmd+Q and re-open it**.

### `text_field_sample` is null for every snapshot of one specific app
- The app's Accessibility tree may not expose the focused element correctly. Worth running the diagnostic script `~/.local/share/tracker/scripts/ax_inspect.py` (if present) with that app focused.

### Dashboard says "stale" / greyed out
- The browser can't reach the local server. Check the server is still running in its terminal. Or the daemon isn't running — `track start` again.

### Subgoal saves but doesn't show under a goal
- The subgoal's `parent_goal` field doesn't match any current main goal exactly (case + whitespace sensitive). It will appear in the "Other" bucket at the bottom of the goals card. The user can fix by editing the goal text or by deleting the subgoal and re-adding under the right goal.

### Screenshots are taken but never analysed
- Check `~/.tracker/raw_responses/` for vision call errors after `track end`. Also `screenshot_analysed = 1` should be set in the DB row after analysis. If 0, the call either failed silently or never ran.

## How to inspect the SQLite database

```bash
sqlite3 ~/.tracker/tracker.db
.tables                                    # list tables
SELECT count(*) FROM snapshots;            # how many snapshots total
SELECT app_name, count(*) FROM snapshots
  GROUP BY app_name ORDER BY 2 DESC LIMIT 20;
SELECT count(*) FROM snapshots WHERE text_field_sample IS NOT NULL;
SELECT count(*) FROM snapshots WHERE screenshot_path IS NOT NULL AND screenshot_analysed = 0;
.quit
```

## How to update tracker

Re-run the installer:
```bash
curl -fsSL <REPO_URL>/install.sh | bash
```

It pulls latest code and refreshes deps. User data in `~/.tracker/` is untouched.

## When in doubt

- Have the user run `track --help` and `track <command> --help` first.
- Have them paste the full error message including any traceback.
- Check `~/.tracker/raw_responses/` for the most recent file if it's an analysis error.
- The code is at `~/.local/share/tracker/`. You can read it directly to see what a command does.

---

**Now describe your problem below this line and Claude will help.**
