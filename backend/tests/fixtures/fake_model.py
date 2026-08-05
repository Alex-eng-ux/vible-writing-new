from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class FakeModelError(RuntimeError):
    """Raised to simulate a dependency or model failure for tests."""


@dataclass
class FakeModelProvider:
    """Deterministic, test-only model provider.

    Not enabled by default in any production configuration. Returns a fixed
    structured result per fixture and can inject clarification, conflict,
    budget-exhausted or dependency-unavailable failures.
    """

    fixture: dict[str, Any] | None = None
    failure: str | None = None
    clarification: str | None = None
    calls: int = field(default=0, init=False)

    def invoke(self, prompt: str | None = None) -> Any:
        self.calls += 1
        if self.failure == "dependency_unavailable":
            raise FakeModelError("dependency unavailable")
        if self.failure == "budget_exhausted":
            raise FakeModelError("budget exhausted")
        if self.clarification is not None:
            return {"status": "needs_clarification", "questions": [self.clarification]}
        if self.fixture is not None:
            return self.fixture
        return {"status": "ready", "text": "fixture-free deterministic result"}

    def supports_structured(self) -> bool:
        return True
