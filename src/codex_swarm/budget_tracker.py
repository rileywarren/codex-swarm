from __future__ import annotations

from dataclasses import dataclass, field

from .models import BudgetConfig, BudgetSnapshot, TokenUsage


MODEL_PRICE_PER_1K = {
    "o3": (0.010, 0.030),
    "o4-mini": (0.003, 0.012),
}


@dataclass
class BudgetTracker:
    config: BudgetConfig
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    warned: bool = False
    worker_costs: dict[str, float] = field(default_factory=dict)

    def estimate_usage_from_text(self, text: str) -> TokenUsage:
        # Coarse fallback: ~4 chars/token split as output tokens.
        output_tokens = max(1, len(text) // 4)
        return TokenUsage(input_tokens=0, cached_input_tokens=0, output_tokens=output_tokens)

    def estimate_cost(self, model: str, usage: TokenUsage) -> float:
        input_price, output_price = MODEL_PRICE_PER_1K.get(model, (0.004, 0.012))
        billable_input = max(0, usage.input_tokens - usage.cached_input_tokens)
        return (billable_input / 1000.0) * input_price + (usage.output_tokens / 1000.0) * output_price

    def add_usage(self, usage: TokenUsage, model: str, worker_id: str | None = None) -> BudgetSnapshot:
        cost = self.estimate_cost(model, usage)

        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_cost += cost

        if worker_id:
            self.worker_costs[worker_id] = self.worker_costs.get(worker_id, 0.0) + cost

        if not self.warned and self.config.max_total_cost > 0:
            pct = (self.total_cost / self.config.max_total_cost) * 100
            if pct >= self.config.warn_at_percent:
                self.warned = True

        return self.snapshot()

    def snapshot(self) -> BudgetSnapshot:
        total_tokens = self.total_input_tokens + self.total_output_tokens
        return BudgetSnapshot(
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            total_tokens=total_tokens,
            total_cost=round(self.total_cost, 6),
            warned=self.warned,
        )

    def can_spawn_worker(self) -> tuple[bool, str]:
        total_tokens = self.total_input_tokens + self.total_output_tokens
        if self.config.max_total_tokens > 0 and total_tokens >= self.config.max_total_tokens:
            return False, "max_total_tokens exceeded"

        if self.config.max_total_cost > 0 and self.total_cost >= self.config.max_total_cost:
            return False, "max_total_cost exceeded"

        return True, "ok"

    def worker_within_budget(self, worker_id: str) -> tuple[bool, str]:
        cost = self.worker_costs.get(worker_id, 0.0)
        if self.config.max_worker_cost > 0 and cost >= self.config.max_worker_cost:
            return False, "max_worker_cost exceeded"
        return True, "ok"
