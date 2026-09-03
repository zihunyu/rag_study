from __future__ import annotations

from pathlib import Path

import pytest
from ragkb.application.provider_budget import (
    BudgetedJsonTransport,
    ProviderBudgetExceeded,
    ProviderBudgetLimits,
)
from ragkb.infrastructure.provider_budget import SQLiteProviderBudgetLedger


class _Transport:
    real_network = True

    def post_json(self, url, *, headers, payload, timeout):
        del url, headers, payload, timeout
        return {"usage": {"prompt_tokens": 2, "completion_tokens": 1}, "ok": True}


def test_budget_is_reserved_before_calls_and_actual_usage_releases_reserve(
    tmp_path: Path,
) -> None:
    ledger = SQLiteProviderBudgetLedger(tmp_path / "budget.sqlite3", ProviderBudgetLimits(2, 20, 5))
    transport = BudgetedJsonTransport(_Transport(), ledger, provider_role="generator")

    assert transport.post_json("https://example", headers={}, payload={"max_tokens": 4}, timeout=1)[
        "ok"
    ]
    assert ledger.usage().calls == 1
    assert ledger.usage().input_tokens == 2
    assert ledger.usage().output_tokens == 1


def test_budget_exhaustion_blocks_before_network(tmp_path: Path) -> None:
    ledger = SQLiteProviderBudgetLedger(tmp_path / "budget.sqlite3", ProviderBudgetLimits(1, 2, 1))
    transport = BudgetedJsonTransport(_Transport(), ledger, provider_role="verifier")

    with pytest.raises(ProviderBudgetExceeded, match="BUDGET_EXHAUSTED"):
        transport.post_json(
            "https://example",
            headers={},
            payload={"content": "too much input", "max_tokens": 1},
            timeout=1,
        )
    assert ledger.usage().calls == 0
