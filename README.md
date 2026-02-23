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

In TUI mode, press `d` to open the model picker.  
Selected defaults are saved to `~/.codex-swarm/config.yaml` and applied on future runs.

If you installed `codex-swarm` as a global uv tool, refresh after updates:

```bash
uv tool install --reinstall --from /Users/rileywarren/Projects/codex-swarm codex-swarm
```

Headless examples:

```bash
codex-swarm fan-out --tasks tasks.json
codex-swarm pipeline --steps pipeline.yaml
codex-swarm swarm --tasks tasks.json --strategy debate
```

Generate native-tool integration guide:

```bash
codex-swarm patch --output docs/patch-guide.md
```

## Architecture Highlights

- Supervisor process (`codex exec --json`) emits virtual tool blocks.
- Orchestrator parses dispatches and launches workers in isolated git worktrees.
- Worker results are compressed and injected back via `.codex-swarm-response.md`.
- Optional Unix socket IPC enables native Codex tool patch integration.
- Textual TUI provides live observability and operator controls.

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
