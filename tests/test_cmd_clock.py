import json
import subprocess
import sys

from conftest import ROOT, run_hook
from cmd_clock.core import advise, handle, history, stem, DEFAULTS


def post(project, cmd, ms, ok=True, error=None):
    ev = {"cwd": str(project), "tool_name": "Bash", "tool_input": {"command": cmd}, "duration_ms": ms}
    if ok:
        ev.update(hook_event_name="PostToolUse", tool_response={"stdout": "", "stderr": "", "interrupted": False})
    else:
        ev.update(hook_event_name="PostToolUseFailure", error=error or "Exit code 1\nboom")
    return handle(ev)


def pre(project, cmd, timeout=None, bg=False):
    ti = {"command": cmd}
    if timeout:
        ti["timeout"] = timeout
    if bg:
        ti["run_in_background"] = True
    return handle({"hook_event_name": "PreToolUse", "cwd": str(project), "tool_name": "Bash", "tool_input": ti})


def test_stem():
    assert stem("cd web && npm test -- --watch=false 2>&1 | tail -30") == "npm test"
    assert stem("python3 -m pytest -q tests/") == "python -m pytest"
    assert stem("FOO=1 cargo test --release") == "cargo test"


def test_fast_commands_never_warn(project):
    for _ in range(3):
        post(project, "ls -la", 40)
    assert pre(project, "ls -la") is None


def test_slow_command_warns_against_default_timeout(project):
    for ms in (150_000, 170_000, 160_000):
        post(project, "npm test", ms)
    out = pre(project, "npm test")
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "2m30s–2m50s" in text and "timeout for this call is 2m00s" in text
    assert "run_in_background" in text
    assert pre(project, "npm test", timeout=600_000) is None
    assert pre(project, "npm test", bg=True) is None


def test_timeouts_count_as_lower_bound(project):
    post(project, "make e2e", 120_000, ok=False, error="Command timed out after 2m 0s")
    rows, how = history(str(project), "make e2e", 3)
    assert rows == [(120_000, 1)] and how == "exact"
    text = pre(project, "make e2e")["hookSpecificOutput"]["additionalContext"]
    assert "hit the timeout" in text


def test_similar_commands_need_three_samples(project):
    post(project, "pytest tests/a", 200_000)
    post(project, "pytest tests/b", 210_000)
    assert pre(project, "pytest tests/c") is None
    post(project, "pytest tests/d", 220_000)
    text = pre(project, "pytest tests/c")["hookSpecificOutput"]["additionalContext"]
    assert "`pytest`-style" in text


def test_over_max_recommends_background(project):
    for ms in (900_000, 950_000):
        post(project, "./slow.sh", ms)
    text = pre(project, "./slow.sh", timeout=600_000)["hookSpecificOutput"]["additionalContext"]
    assert "exceeds the 10m00s Bash maximum" in text


def test_projects_are_separate(project, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    for _ in range(3):
        post(project, "npm test", 300_000)
    assert pre(other, "npm test") is None


def test_background_runs_not_recorded(project):
    ev = {"hook_event_name": "PostToolUse", "cwd": str(project), "tool_name": "Bash", "duration_ms": 3,
          "tool_input": {"command": "npm run dev", "run_in_background": True},
          "tool_response": {"stdout": "", "backgroundTaskId": "b1"}}
    handle(ev)
    assert history(str(project), "npm run dev", 1) == ([], "")


def test_cli_and_script(project, isolated):
    for _ in range(2):
        out = run_hook({"hook_event_name": "PostToolUse", "cwd": str(project), "tool_name": "Bash",
                        "duration_ms": 61_000, "tool_input": {"command": "go test ./..."},
                        "tool_response": {"stdout": "ok"}}, isolated)
        assert out == {}
    import os
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "cmd-clock"), "--project", str(project)],
                       capture_output=True, text=True, env=dict(os.environ, HOME=str(isolated)))
    assert "`go test ./...`" in r.stdout and "| 1m01s |" in r.stdout
