# cmd-clock

Records how long every Bash command takes per repository, from the `duration_ms` that Claude Code
reports. Before a command runs, it tells Claude when history says the command will outlast the call's
timeout (the 2-minute default, or the `timeout` Claude passed):

> cmd-clock: in this repo this command took 2m30s–2m50s (p90 2m48s) over the last 3 run(s). The timeout
> for this call is 2m00s. A timeout of about 252000 ms or run_in_background would fit it.

Runs that hit the timeout count as lower bounds. Unseen commands fall back to similar ones (same
program and subcommand, e.g. `pytest`, `npm test`) after three samples.

**Hooks:** `PreToolUse`, `PostToolUse`, `PostToolUseFailure` on Bash.
**CLI:** `cmd-clock top [-n 15]` shows the slowest commands in this repo; `cmd-clock forget` clears them.
**Config:** `warn_ratio` (0.8), `min_warn_ms` (30000), `keep_per_key`, `min_samples_stem`.

## Install

From Anthropic's plugin directory, once listed: search for `cmd-clock` in `/plugin directory` in Claude Code, or under **Customize > Plugins** on claude.ai.

To try it from a clone for one session: `claude --plugin-dir /path/to/cmd-clock`.

This plugin is for Claude Code: it is built on hooks and a command-line tool in `bin/`, which claude.ai chat does not load.

## Configuration

Settings are read from `~/.claude/cmd-clock.json` and then `<project>/.claude/cmd-clock.json`. The project file wins, objects merge by key, and a broken file is skipped and logged.

## What it runs and stores

It is Python 3.9+ standard library only, makes no network requests, and sends nothing anywhere. Every hook fails open: an internal error is logged to `~/.claude/cmd-clock/errors.log` and the hook exits 0.

- Stores each Bash command's text and duration, per repository, in `~/.claude/cmd-clock/runs.sqlite`.
- Runs no commands of its own.

## Development

```
python3 -m pytest -q
claude plugin validate --strict .
```

`docs/HOOK_SCHEMA.md` records the hook payloads this plugin depends on.

## License

MIT. See `LICENSE`.
