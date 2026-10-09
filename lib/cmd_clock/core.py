"""Record Bash command durations per repo; warn before a run that will outlast its timeout."""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

import hookio

PLUGIN = "cmd-clock"

DEFAULTS = {
    "warn_ratio": 0.8,          # warn when expected duration >= ratio * timeout
    "min_warn_ms": 30_000,      # never warn about commands that usually finish faster than this
    "keep_per_key": 50,
    "min_samples_stem": 3,      # fuzzy (stem) matches need this many samples
}

BASH_DEFAULT_MS = 120_000
BASH_MAX_MS = 600_000
TIMEOUT_RE = re.compile(r"(?i)\btimed out\b|\btimeout\b.*\bexceeded\b")


def default_timeout_ms() -> int:
    try:
        return int(os.environ.get("BASH_DEFAULT_TIMEOUT_MS") or BASH_DEFAULT_MS)
    except ValueError:
        return BASH_DEFAULT_MS


def normalize(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip())


FILLER = {"cd", "echo", "export", "source", ".", "tee", "head", "tail", "grep", "sort", "wc",
          "less", "cat", "true", "set", "sleep"}


def stem(command: str) -> str:
    """Program + subcommand of the first substantive segment, e.g. `npm test`, `python -m pytest`."""
    segs = [s for s in hookio.segments(command) if Path(s[0]).name not in FILLER]
    if not segs:
        return normalize(command)[:80]
    words = segs[0]
    prog = Path(words[0]).name
    if prog.startswith("python") and len(words) > 2 and words[1] == "-m":
        return f"python -m {words[2]}"
    sub = next((w for w in words[1:] if not w.startswith("-")), "")
    if not re.fullmatch(r"[a-z][a-z0-9:_-]*", sub):
        sub = ""  # a path or value, not a subcommand
    return f"{prog} {sub}".strip()


def db_path() -> Path:
    return hookio.data_dir(PLUGIN) / "runs.sqlite"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path(), timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS runs (
        project TEXT, key TEXT, stem TEXT, ms INTEGER, ok INTEGER, timed_out INTEGER, ts REAL)""")
    con.execute("CREATE INDEX IF NOT EXISTS runs_key ON runs(project, key)")
    con.execute("CREATE INDEX IF NOT EXISTS runs_stem ON runs(project, stem)")
    return con


def record(project: str, command: str, ms: int, ok: bool, timed_out: bool, keep: int) -> None:
    key = normalize(command)
    with connect() as con:
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                    (project, key, stem(command), int(ms), int(ok), int(timed_out), time.time()))
        con.execute("""DELETE FROM runs WHERE project=? AND key=? AND rowid NOT IN (
                         SELECT rowid FROM runs WHERE project=? AND key=? ORDER BY ts DESC LIMIT ?)""",
                    (project, key, project, key, keep))


def history(project: str, command: str, min_stem: int) -> tuple[list[tuple[int, int]], str]:
    """[(ms, timed_out)] newest first, and which match was used ('exact' / 'similar')."""
    with connect() as con:
        rows = con.execute("SELECT ms, timed_out FROM runs WHERE project=? AND key=? ORDER BY ts DESC LIMIT 20",
                           (project, normalize(command))).fetchall()
        if rows:
            return rows, "exact"
        rows = con.execute("SELECT ms, timed_out FROM runs WHERE project=? AND stem=? ORDER BY ts DESC LIMIT 20",
                           (project, stem(command))).fetchall()
    return (rows, "similar") if len(rows) >= min_stem else ([], "")


def fmt(ms: float) -> str:
    s = int(round(ms / 1000))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m{s:02d}s" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def p90(values: list[int]) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    idx = 0.9 * (len(v) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (idx - lo)


def advise(project: str, command: str, timeout_ms: Optional[int], cfg: dict) -> Optional[str]:
    rows, how = history(project, command, int(cfg["min_samples_stem"]))
    if not rows:
        return None
    durations = [ms for ms, _ in rows]
    timeouts = sum(1 for _, t in rows if t)
    expected = max(p90(durations), max(ms for ms, t in rows if t) if timeouts else 0)
    limit = timeout_ms or default_timeout_ms()
    if expected < cfg["min_warn_ms"] or expected < cfg["warn_ratio"] * limit:
        return None
    label = f"`{stem(command)}`-style commands" if how == "similar" else "this command"
    lo, hi = fmt(min(durations)), fmt(max(durations))
    span = lo if lo == hi else f"{lo}–{hi}"
    parts = [f"cmd-clock: in this repo {label} took {span} (p90 {fmt(p90(durations))}) "
             f"over the last {len(rows)} run(s)"]
    if timeouts:
        parts[0] += f"; {timeouts} of them hit the timeout, so the real time is longer"
    parts[0] += f". The timeout for this call is {fmt(limit)}."
    if expected * 1.25 <= BASH_MAX_MS:
        parts.append(f"A timeout of about {int(min(BASH_MAX_MS, expected * 1.5)) // 1000 * 1000} ms "
                     "or run_in_background would fit it.")
    else:
        parts.append(f"That exceeds the {fmt(BASH_MAX_MS)} Bash maximum, so run_in_background fits it.")
    return " ".join(parts)


def handle(event: dict) -> Optional[dict]:
    if not hookio.is_bash_event(event):
        return None
    name = event.get("hook_event_name")
    project = str(hookio.project_dir(event))
    cfg = hookio.load_config(PLUGIN, Path(project), DEFAULTS)
    if name in ("PostToolUse", "PostToolUseFailure"):
        run = hookio.BashRun(event)
        if run.background or run.duration_ms is None or not run.command:
            return None
        timed_out = (not run.ok) and bool(TIMEOUT_RE.search(run.output or "") or
                                          TIMEOUT_RE.search(event.get("error") or ""))
        record(project, run.command, run.duration_ms, run.ok, timed_out, int(cfg["keep_per_key"]))
        return None
    if name == "PreToolUse":
        ti = event.get("tool_input") or {}
        if ti.get("run_in_background") or not ti.get("command"):
            return None
        text = advise(project, ti["command"], ti.get("timeout"), cfg)
        return hookio.context("PreToolUse", text) if text else None
    return None
