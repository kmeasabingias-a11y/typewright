# 02 — `src/typewright/logging_config.py`

## What this file is for

This file decides **how TypeWright writes down what it's doing** while it runs. Every
"a request came in", "the code couldn't be parsed", "finished in 40 milliseconds" note
the program makes for itself is a *log*. This file sets up where those notes go and what
they look like.

Think of it like the ship's logbook. As the ship sails, the crew jots down events —
departure time, weather, anything unusual. Later, if something went wrong, you read the
logbook to find out what happened and when. This file sets up TypeWright's logbook so
that every entry is written in the same neat format, all in one place.

In Phase 1 we keep this deliberately tiny: just enough to see useful messages in the
terminal. Fancy logging (machine-readable JSON, tracing every AI call) belongs to later
phases, so we don't build it yet.

---

## A mental model: what is a "log level"?

Not every message is equally important. A **log level** is just a label that says how
serious a message is. Python's common levels, from quietest to loudest:

- **DEBUG** — tiny details, useful only when hunting a bug.
- **INFO** — normal "this happened" notes. (Our default.)
- **WARNING** — something looks off, but the app keeps going.
- **ERROR** — something actually failed.

You pick a level, and the program shows that level **and everything more serious**, while
hiding the rest. Set it to `INFO` and you see INFO, WARNING, ERROR — but not the noisy
DEBUG chatter. Set it to `DEBUG` while bug-hunting and suddenly you see everything.

That level comes from the settings panel (`config.py`), so you can turn the volume up or
down without touching code.

---

## The whole file

```python
"""Logging setup for TypeWright.

Phase 1 keeps this to a single stdlib ``logging.basicConfig`` call. Structured
logging and LLM trace observability (Langfuse) are explicit later-phase concerns
(see DECISIONS.md D10); we do not pull them forward.
"""

import logging

from .config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure root logging from settings. Call once at app startup."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
```

---

## Step-by-step

### The imports

```python
import logging
from .config import Settings
```

- **`logging`** — Python's built-in logging toolkit. It's already on every Python
  install; we just configure it. We don't add any external library here.
- **`Settings`** — imported only so we can say "this function expects a settings
  object." We need it to know which log level the user chose.

### The function

```python
def configure_logging(settings: Settings) -> None:
```

This is a one-job function: set up logging. A few things to notice:

- It **takes the settings as an argument** rather than fetching them itself. That keeps
  it simple and predictable — it only does what it's handed, with no hidden reaching-out.
  Whoever starts the app will hand it the settings.
- It **returns nothing** (`-> None`). It works by *changing* the global logging setup as
  a side effect. That's why you call it **once**, right when the app starts.

### The one real line of work

```python
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
```

`logging.basicConfig(...)` is Python's "set up logging with sensible defaults in one
call" helper. We give it three things:

- **`level=settings.log_level.upper()`** — the volume knob from the settings. `.upper()`
  turns `"info"` into `"INFO"`, so it works whether the user typed it lowercase or not.

- **`format=...`** — the template for each log line. The `%(...)s` pieces are
  fill-in-the-blanks that Python replaces for every message:
  - `%(asctime)s` → the time the message happened
  - `%(levelname)-8s` → the level (INFO, ERROR…), padded to 8 characters so the columns
    line up neatly
  - `%(name)s` → which part of the program produced the message
  - `%(message)s` → the actual message text

  So a line ends up looking like:
  `2026-06-09T14:03:11 INFO     typewright.main | service starting`

- **`datefmt="%Y-%m-%dT%H:%M:%S"`** — how to write the time: year-month-day, a `T`, then
  hours:minutes:seconds. A clear, standard, sortable timestamp.

That's the whole file. Once this runs, anywhere else in the code can do
`logging.getLogger(__name__).info("something happened")` and the message comes out in
this exact format.

---

## What could go wrong

### 1. Never calling it
If `configure_logging` is never run at startup, Python falls back to a bare-bones default
that only shows WARNING and above — so all your friendly INFO messages silently vanish.
You'd think the app is doing nothing. The fix is to call this once when the app starts
(which `main.py` will do).

### 2. Calling it after something already logged
`basicConfig` politely does nothing if logging has *already* been set up by an earlier
message. So if some code logs before this runs, your nice format never takes effect.
Rule: configure logging first, before anything else gets a chance to log.

### 3. Naming this file `logging.py`
We named it `logging_config.py` on purpose. If we'd named it `logging.py`, it could be
confused with Python's own built-in `logging` module and cause baffling import errors.
Small naming choice, real headache avoided.

### 4. A misspelled level
If someone sets the level to something Python doesn't recognize (a typo like `"INFOO"`),
logging setup can fail. For now we trust the value; if it becomes a problem we can
validate it in `config.py`.

### 5. Accidentally logging secrets or user code
This file doesn't do it, but it's worth saying: because logs are easy and tidy, it's
tempting to log everything — including the user's submitted code or an API key. Don't.
Logs often get shipped to other systems, and a secret in a log is a leaked secret.

---

## Summary

`logging_config.py` sets up TypeWright's logbook in a single, small function. It reads
the desired log level from the settings and tells Python's built-in logging to print
messages in one consistent, timestamped format. Call it once at startup, and from then on
every part of the app writes neat, uniform log lines.

It's intentionally minimal in Phase 1. Richer logging — machine-readable output and
tracing of every AI call — is a later-phase job, and we're not pulling it forward.

---

## Change history

- **2026-06-09** — Created in Phase 1, Unit 2. Single `configure_logging` function using
  stdlib `logging.basicConfig`. Level driven by `config.py`.
- **2026-06-28** — Phase 9 (Unit 4, D54): added an optional `_JsonFormatter`, selected by
  `log_format="json"` (default `"text"` keeps the human-readable console format unchanged). The JSON
  formatter emits each record as one JSON object and merges the structured `trace` fields the
  per-analysis traces attach via `extra` (`tracing.py`, unit 28) — so an aggregator gets clean JSON.
