from __future__ import annotations

from codex_swarm.patch_guide import generate_patch_guide


def test_patch_guide_contains_required_sections() -> None:
    guide = generate_patch_guide()
    assert "spawn_agent" in guide
    assert "CODEX_SWARM_SOCKET" in guide
    assert "does not auto-modify" in guide
