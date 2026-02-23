from __future__ import annotations

from codex_swarm.dispatch_parser import parse_agent_message_from_json_line, parse_dispatch_blocks


def test_parse_all_dispatch_blocks() -> None:
    text = """
```spawn_agent
{"task":"A","scope":["src/**"],"context":"c","priority":"high","return_format":"summary"}
```
```check_workers
{"worker_ids":["w1"]}
```
```merge_results
{"worker_ids":["w1"],"resolve_conflicts":"abort"}
```
```spawn_swarm
{"tasks":[{"task":"B","scope":[],"context":"","priority":"normal","return_format":"summary"}],"strategy":"fan-out","wait":true}
```
"""
    requests = parse_dispatch_blocks(text)
    assert len(requests) == 4
    assert requests[0].tool == "spawn_agent"
    assert requests[1].tool == "check_workers"
    assert requests[2].tool == "merge_results"
    assert requests[3].tool == "spawn_swarm"


def test_parse_dispatch_with_fuzzy_trailing_comma() -> None:
    text = """
```spawn_agent
{"task":"A","scope":["src/**",],"context":"c","priority":"high","return_format":"summary",}
```
"""
    requests = parse_dispatch_blocks(text)
    assert len(requests) == 1
    assert requests[0].payload["task"] == "A"


def test_parse_agent_message_line() -> None:
    line = '{"type":"item.completed","item":{"type":"agent_message","text":"hello"}}'
    assert parse_agent_message_from_json_line(line) == "hello"


def test_parse_spawn_swarm_workers_shape() -> None:
    text = """
```spawn_swarm
{
  "task":"Refactor authentication flow with tests",
  "priority":"high",
  "workers":[
    {"task":"Audit auth code","scope":["src/auth/**"],"return_format":"summary"},
    {"objective":"Add test coverage","scope":["tests/**"],"priority":"normal","return_format":"summary+test-results"}
  ],
  "merge_strategy":"non-conflicting"
}
```
"""
    requests = parse_dispatch_blocks(text)
    assert len(requests) == 1
    req = requests[0]
    assert req.tool == "spawn_swarm"
    assert len(req.payload["tasks"]) == 2
    assert req.payload["tasks"][1]["task"] == "Add test coverage"


def test_invalid_dispatch_block_is_skipped() -> None:
    text = """
```spawn_agent
{"context":"missing task"}
```
```check_workers
{"worker_ids":["w1"]}
```
"""
    requests = parse_dispatch_blocks(text)
    assert len(requests) == 1
    assert requests[0].tool == "check_workers"
