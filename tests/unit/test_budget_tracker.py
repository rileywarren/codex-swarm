from __future__ import annotations

from codex_swarm.budget_tracker import BudgetTracker
from codex_swarm.models import BudgetConfig, TokenUsage


def test_budget_accumulates_usage_and_warns() -> None:
    tracker = BudgetTracker(BudgetConfig(max_total_cost=0.001, warn_at_percent=50))
    usage = TokenUsage(input_tokens=1000, cached_input_tokens=0, output_tokens=1000)
    snapshot = tracker.add_usage(usage, "o3", worker_id="w1")

    assert snapshot.total_tokens == 2000
    assert snapshot.total_cost > 0
    assert snapshot.warned is True


def test_worker_budget_limit() -> None:
    tracker = BudgetTracker(BudgetConfig(max_worker_cost=0.0001))
    usage = TokenUsage(input_tokens=1000, cached_input_tokens=0, output_tokens=1000)
    tracker.add_usage(usage, "o3", worker_id="w1")
    allowed, _ = tracker.worker_within_budget("w1")
    assert allowed is False
