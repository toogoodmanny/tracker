# Tracker — Coding Practices & Conventions

These rules apply to every file in this repo. They exist so that both human
developers and AI coding agents have unambiguous patterns to follow. Violating
them is a build error, not a style preference.

---

## 1. Test-Driven Development

- Write the test first. No new function ships without a corresponding test.
- Tests live in `tests/` mirroring the source tree:
  `tracker/collectors/window.py` → `tests/collectors/test_window.py`
- Run `pytest` before every commit. CI will enforce this.
- Mocks go in `tests/conftest.py` — never inline in test files.
- Every public function must have at least one happy-path and one error-path test.

## 2. No Polluting Root Environment

- All Python dependencies live inside the project virtualenv at `.venv/`.
- Never `pip install` globally. Always activate the venv first:
  `source .venv/bin/activate`
- `pyproject.toml` is the single source of truth for dependencies.
- `requirements.txt` is generated from pyproject, never hand-edited.
- macOS system Python (`/usr/bin/python3`) is never used directly.

## 3. No Bare Catch-Alls

```python
# FORBIDDEN
try:
    do_something()
except Exception:
    pass

# FORBIDDEN
try:
    do_something()
except:
    pass

# REQUIRED — name the exception, log it, decide what to do
try:
    do_something()
except PermissionError as exc:
    logger.warning("accessibility permission denied: %s", exc)
    return None
except OSError as exc:
    logger.error("OS error reading window: %s", exc)
    raise
```

Every `except` clause must name a specific exception type and either
re-raise, return a typed fallback, or log with explanation.

## 4. No Raw SQL Outside the Abstraction Layer

- All SQL lives in `tracker/db/queries.py` and `tracker/db/schema.py`.
- No `conn.execute("SELECT ...")` anywhere outside `tracker/db/`.
- Application code calls typed repository functions:
  `db.snapshots.insert(snapshot)` not `db.execute("INSERT INTO ...")`.
- Schema migrations live in `tracker/db/migrations/` numbered sequentially.

## 5. No Raw Input in the CLI

- All CLI input goes through `tracker/cli/prompts.py` helper functions.
- Never use bare `input()` in command handlers.
- Every prompt has a type hint for its return value.
- Multi-line input uses the `multiline_prompt()` helper which handles
  Ctrl-C gracefully.

## 6. No Dynamic Imports

```python
# FORBIDDEN
module = importlib.import_module(f"tracker.collectors.{name}")

# REQUIRED — explicit imports only
from tracker.collectors.window import WindowCollector
```

If you need runtime dispatch, use a registry dict with explicit keys.
Dynamic imports hide dependencies and break static analysis.

## 7. Unique Function Names Enforced

- No two public functions in the codebase share a name.
- Use the module prefix if needed: `parse_window_title` not `parse`.
- Before writing a new function, search the codebase first.
- Helper functions that are truly local are prefixed with `_`.

## 8. Modular Boundaries

The codebase is divided into layers. Each layer may only import from layers
below it, never sideways or upward:

```
cli           → analysis, db, collectors
analysis      → db, config
collectors    → db, config
db            → config
config        → (nothing internal)
```

Circular imports are a build error. Use `from __future__ import annotations`
and string forward references if you hit a type-hint cycle.

## 9. Known Patterns — Use These, Don't Invent

**Collector pattern** — every data source is a class:
```python
class WindowCollector:
    def collect(self) -> WindowSnapshot | None:
        ...
```

**Repository pattern** — every DB table has a repo:
```python
class SnapshotRepository:
    def insert(self, snapshot: Snapshot) -> int: ...
    def get_by_day(self, date: datetime.date) -> list[Snapshot]: ...
```

**Result type for fallible ops** — use `dataclasses` not exceptions for
expected failures:
```python
@dataclass
class CollectionResult:
    snapshot: WindowSnapshot | None
    error: str | None
    success: bool
```

**Config via dataclass** — never read env vars or files inline:
```python
cfg = load_config()  # returns typed Config dataclass
```

## 10. Simple Core, Complexity Pushed Up

- `tracker/core/` contains only pure functions with no I/O.
- I/O (filesystem, network, Accessibility API, SQLite) lives in collectors,
  db, and analysis layers.
- The daemon loop in `daemon.py` is a thin orchestrator — no business logic.
- LLM prompt templates are plain text files in `tracker/prompts/`, never
  f-strings scattered through code.

## 11. No Hidden Magic

- No `__init__.py` that silently re-exports things.
- No metaclasses, descriptors, or `__getattr__` tricks.
- Configuration is explicit: if a function needs a config value, it takes
  it as a parameter or receives a `Config` object.
- No global mutable state. The SQLite connection is passed explicitly,
  not stored in a module-level variable.

## 12. Logging, Not Print

- All output uses the `logging` module. No `print()` in library code.
- CLI output (user-facing) uses `tracker/cli/output.py` helpers.
- Log levels: DEBUG for poll cycles, INFO for state changes, WARNING for
  recoverable errors, ERROR for failures requiring attention.

---

## File Naming Conventions

| What | Convention | Example |
|---|---|---|
| Python modules | snake_case | `window_collector.py` |
| Test files | `test_` prefix | `test_window_collector.py` |
| Prompt templates | kebab-case `.txt` | `daily-analysis.txt` |
| Migration files | numbered | `001_initial_schema.sql` |
| Config files | kebab-case | `config.json` |
| Report outputs | ISO date | `2025-04-24.html` |

---

## Git Conventions

- Commits: `type(scope): message` — e.g. `feat(collectors): add text field sampler`
- Types: `feat`, `fix`, `test`, `refactor`, `docs`, `chore`
- No commit without passing tests.
- Branch per feature: `feat/youtube-watcher`, `fix/aw-permissions`
