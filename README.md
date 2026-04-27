# Tracker

A personal time tracker for people with ADHD (and anyone else who wants brutal honesty about how they spent their day).

It runs in the background while you work, captures what you're doing every 30 seconds, and at the end of the day uses an LLM (Claude) to write you a personalized report: where you actually spent your time, what triggered each distraction, how it compared to the goals you set in the morning.

It also gives you a **live web dashboard** while you work, where you can see your goals, add subgoals, and watch the timeline update.

> **Heads up.** This is a personal tool. It runs locally on your Mac. It does not share your data with anyone — except that the daily/weekly summary is sent to the Anthropic API (Claude) for analysis. You bring your own API key.

---

## Table of contents

1. [What you'll need](#1-what-youll-need)
2. [Install — step by step](#2-install--step-by-step)
3. [Daily use](#3-daily-use)
4. [The dashboard](#4-the-dashboard)
5. [Getting unstuck (with Claude's help)](#5-getting-unstuck-with-claudes-help)
6. [Privacy and what data goes where](#6-privacy-and-what-data-goes-where)
7. [Troubleshooting](#7-troubleshooting)
8. [Updating to the latest version](#8-updating)
9. [Uninstalling](#9-uninstalling)

---

## 1. What you'll need

A Mac. The tool currently only runs on macOS.

You will need to install three things separately. The installer can't do these for you — they need your permission and a few clicks each:

1. **ActivityWatch** — a free, open-source desktop app that tracks which window is active. Tracker reads from it.
2. **An Anthropic (Claude) API key** — the LLM that writes your daily report. You bring your own key, you pay for your own usage. Daily report ≈ 5–15 cents of API spend.
3. **Google Chrome with developer-mode** (only if you want YouTube + page-content tracking — optional).

Don't worry, the steps are below.

---

## 2. Install — step by step

> **Never used Terminal before?** Open Spotlight (`Cmd+Space`), type "Terminal", press Enter. A window opens where you can paste commands. After pasting each one, press Enter to run it.

### Step 1 — Install Python and git (if you don't already have them)

Most Macs have these. Check by pasting this into Terminal and pressing Enter:

```bash
python3 --version && git --version
```

If both print a version number, skip to Step 2.

If either says "command not found":

```bash
xcode-select --install
```

That's a click-through Apple installer for git. For Python, install Homebrew first if you don't have it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.13
```

### Step 2 — Install ActivityWatch

1. Go to **https://activitywatch.net** and download the macOS installer.
2. Open the downloaded `.dmg` and drag ActivityWatch into Applications.
3. Open ActivityWatch from Applications. It lives in your menu bar (top-right of screen). It will keep running quietly in the background.

> **Note:** ActivityWatch by itself is just a window-title logger. It's the tracker that adds goals, screenshots, text capture, and the daily LLM analysis on top.

### Step 3 — Get an Anthropic API key

1. Go to **https://console.anthropic.com**, sign up if you don't have an account.
2. Add a payment method and load $5 of credit. (A typical day of analysis costs less than $0.20.)
3. Go to **API Keys**, click **Create Key**, and copy the key (it starts with `sk-ant-...`).
4. Paste it somewhere safe for a moment — you'll need it in Step 5.

### Step 4 — Install the tracker

Paste this into Terminal and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/toogoodmanny/tracker/main/install.sh | bash
```

This downloads everything, sets up Python, and creates a `track` command. Takes 1–2 minutes.

When it finishes, you should see a green "Done" message.

If `track` is "command not found" after install, close and re-open Terminal (the PATH needs to refresh).

### Step 5 — Run the setup wizard

```bash
track setup
```

It will ask you about:
- Your Anthropic API key (paste the one from Step 3)
- Your name and what you do
- Your projects (each gets a name + description + keywords that signal you're working on it)
- Your team / collaborators (optional)
- Distractions you want flagged aggressively (e.g. "football, twitter, news")
- Your work hours
- Whether you want feedback in a harsh / encouraging / neutral tone

This takes about 5 minutes. Your answers are saved to `~/.tracker/config.json` and `~/.tracker/CLAUDE.md`.

### Step 6 — Grant macOS permissions

The wizard will pause and ask you to grant 3 permissions to **Terminal**:

1. Open **System Settings → Privacy & Security**
2. Add **Terminal** to:
   - **Accessibility** (for reading window titles + text fields)
   - **Screen Recording** (for screenshots when an unknown app is in focus)
   - **Full Disk Access** (for reading docs you have open)
3. After granting, **fully quit Terminal** (`Cmd+Q`) and re-open it. macOS won't apply the permissions until Terminal restarts.

### Step 7 — (Optional) Load the Chrome extension

This adds YouTube title tracking (so the LLM can see what video you watched) and page-content sampling (so it can tell which Reddit topic you fell into).

1. Open Chrome and go to `chrome://extensions`
2. Toggle **Developer mode** on (top-right of the page)
3. Click **Load unpacked**
4. Navigate to `~/.local/share/tracker/chrome_extension` and select the folder
5. The extension should appear with no errors

### You're done. Run your first session:

```bash
track start
```

Work normally for a couple of hours. Then:

```bash
track end
```

It generates a self-contained HTML report, opens it in your browser, and saves it under `~/.tracker/reports/`.

---

## 3. Daily use

| Command | What it does |
|---|---|
| `track plan` | Set goals for tomorrow (or today if you haven't started yet) |
| `track start` | Begin tracking. Daemon runs in the background. |
| `track status` | Quick text summary of the current session |
| `track dashboard` | Open the live web dashboard at http://127.0.0.1:27183 |
| `track break 30` | Mark a 30-min intentional break (excluded from drift) |
| `track correct "..."` | Tell the tracker it misclassified something |
| `track note "..."` | Add a freeform note to the session |
| `track sleep` | Pause tracking until tomorrow |
| `track end` | Stop tracking and generate today's report |
| `track week` | Generate the weekly report (run on Sunday) |

---

## 4. The dashboard

While tracking, run in a second Terminal tab:

```bash
track dashboard
```

It opens **http://127.0.0.1:27183** in your browser. The page shows:

- A status pill (tracking / idle)
- Your goals for the day, editable inline (one per line, save button gives a green ✓ when saved)
- Subgoals nested under each main goal — these are your concrete steps to stay on track
- A **timeline** that groups consecutive same-app activity into single rows. Refreshes every 30 seconds, only redraws when you switch apps.

The dashboard binds to `127.0.0.1` only — it's never reachable from another machine.

---

## 5. Getting unstuck (with Claude's help)

If something doesn't work and the troubleshooting section below doesn't fix it, the easiest thing is to ask Claude. We've shipped a context file that gives Claude everything it needs to help.

**To use it:**

1. Open Claude (either [claude.ai](https://claude.ai) or Claude Code in a terminal)
2. Open the file at `~/.local/share/tracker/HELP_PROMPT.md` and copy its contents into your conversation
3. Describe what went wrong. Claude can read the prompt context and walk you through fixing it.

You can find the file at this path on your Mac:
```
~/.local/share/tracker/HELP_PROMPT.md
```

Or open it in TextEdit:
```bash
open -e ~/.local/share/tracker/HELP_PROMPT.md
```

---

## 6. Privacy and what data goes where

**Local on your Mac (never leaves):**
- All raw snapshots of what you did (`~/.tracker/tracker.db`)
- Screenshots (`~/.tracker/screenshots/`)
- Text field samples (e.g. what you typed in WhatsApp before sending)

**Sent to Anthropic (Claude API) once a day at `track end`:**
- A condensed JSON summary of your day's snapshots (timestamps, app names, window titles, URLs, last 200 chars of text fields)
- The screenshots that were flagged by the trigger logic (when you were in an unknown app or stalled)
- Your goals + corrections + your CLAUDE.md context

**Never sent anywhere:**
- Your raw text-field samples beyond the 200 char tail per snapshot
- Files you have open
- Anything from outside your tracked sessions

You can inspect exactly what's about to be sent. Run `track end` and the raw prompt is saved to `~/.tracker/raw_responses/` after the call.

---

## 7. Troubleshooting

### `track: command not found`
Close and re-open Terminal. If still broken: `~/.local/share/tracker/.venv/bin/track --help` should work directly. If yes, the shim isn't on your PATH — add `export PATH="$PATH:$HOME/.local/bin"` to `~/.zshrc`.

### `ActivityWatch is not running`
Open the ActivityWatch app from Applications. It should appear in your menu bar. The icon means it's running. Then re-run `track start`.

### `Anthropic API error: invalid api key`
Open `~/.tracker/config.json`, check the `anthropic_api_key` field. If wrong, fix it and re-run `track end`.

### `Permission denied` when capturing text fields / screenshots
You either skipped the macOS permissions step, or you granted them to a different app (e.g. iTerm vs Terminal). System Settings → Privacy & Security → check that **the Terminal app you're actually using** is added to Accessibility, Screen Recording, and Full Disk Access. **Quit and re-launch Terminal after granting.**

### The daily report says "max_tokens hit, response truncated"
Your day was very long. The raw response is saved to `~/.tracker/raw_responses/` — open the latest file, fix any incomplete JSON manually, and you have your report data.

### Daily report failed to parse JSON
Same fix — the raw response is saved. The error message tells you the path.

### Dashboard shows "stale" / dimmed
Your `track start` daemon stopped. Check with `ps aux | grep tracker`. Re-run `track start`.

### Something else broken
Use the [Getting unstuck](#5-getting-unstuck-with-claudes-help) section above and ask Claude.

---

## 8. Updating

Re-run the installer at any time:

```bash
curl -fsSL https://raw.githubusercontent.com/toogoodmanny/tracker/main/install.sh | bash
```

It pulls the latest code and refreshes dependencies. Your `~/.tracker/` data stays untouched.

---

## 9. Uninstalling

```bash
rm -rf ~/.local/share/tracker
rm /usr/local/bin/track 2>/dev/null || rm ~/.local/bin/track
```

To also delete your tracking data (the database, reports, screenshots — be sure):
```bash
rm -rf ~/.tracker
```

---

## License

MIT. Use at your own risk. The author makes no claims about anything.

## Credits

Built on top of [ActivityWatch](https://activitywatch.net) for window/AFK tracking, [Anthropic Claude](https://www.anthropic.com) for analysis, and [pyobjc](https://pypi.org/project/pyobjc/) for the macOS Accessibility API.
