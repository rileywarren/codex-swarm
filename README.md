# Codex Swarm

Codex Swarm is an external orchestration layer for Codex CLI that adds supervisor-driven delegation, isolated worker worktrees, and multi-strategy swarm execution.

## Platform

- Supported: macOS (v1)
- Python: 3.12+
- Codex CLI: required in PATH

## Install

```bash
uv sync
```

## Quickstart

```bash
codex-swarm run "Refactor authentication flow with tests"
```

Launch the desktop GUI (v1, macOS):

```bash
codex-swarm gui
```

If `gui.enabled` is set to false in config, the command exits with a clear error.

In TUI mode, press `d` to open the model picker.  
Selected defaults are saved to `~/.codex-swarm/config.yaml` and applied on future runs.

### GUI usage

- Multiple concurrent sessions via tabs:
  - `Supervisor` mode for open-ended objective-based runs.
  - `Strategy` mode for explicit `fan-out`, `pipeline`, `map-reduce`, and `debate` workflows.
- Per-tab runtime isolation:
  - Unique response file: `.codex-swarm-response.<session_id>.md`
  - `ipc.method` is forced to `file_watch` per session.
- Persistent history:
  - Stored in SQLite (default `~/.codex-swarm/history.db`).
  - Keeps the latest `history_max_runs` entries (default 200).
- Live controls include worker cancel, merge, queue pause/resume, and supervisor kill.
- On close, active sessions can be stopped automatically or kept open based on confirmation.

If you installed `codex-swarm` as a global uv tool, refresh after updates:

```bash
uv tool install --reinstall --from /Users/rileywarren/Projects/codex-swarm codex-swarm
```

CLI examples:

```bash
codex-swarm fan-out --tasks tasks.json
codex-swarm pipeline --steps pipeline.yaml
codex-swarm swarm --tasks tasks.json --strategy debate
codex-swarm gui
```

GUI-related config (`config/defaults.yaml`):

```yaml
gui:
  enabled: true
  history_db_path: "~/.codex-swarm/history.db"
  history_max_runs: 200
  max_concurrent_sessions: 6
```

Install options:

```bash
# runtime requirements (GUI command):
uv pip install "PySide6>=6.8.2" "qasync>=0.27.2"

# dev dependency for GUI smoke tests:
uv pip install "pytest-qt>=4.5.0"
```

```bash
codex-swarm patch --output docs/patch-guide.md
```

## Architecture Highlights

- Supervisor process (`codex exec --json`) emits virtual tool blocks.
- Orchestrator parses dispatches and launches workers in isolated git worktrees.
- Worker results are compressed and injected back via `.codex-swarm-response.md`.
- Optional Unix socket IPC enables native Codex tool patch integration.
- Textual TUI provides live observability and operator controls.
- Desktop GUI is available via `codex-swarm gui`, with multi-session, persistent history, and per-session run isolation.

## Failure Handling

- Worker timeout kill with partial diff capture.
- Merge conflicts auto-abort and report back.
- Out-of-scope file edits require explicit supervisor approval (`merge_results`).
- Supervisor dispatch parser is tolerant of common `spawn_swarm` shape variations (for example `workers` vs `tasks`) and skips invalid blocks without crashing the session.

## Project Structure

- `src/codex_swarm/`: runtime modules
- `config/defaults.yaml`: default configuration
- `docs/patch-guide.md`: native integration guide
- `tests/`: unit and integration coverage
