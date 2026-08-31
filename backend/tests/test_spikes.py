from __future__ import annotations

from ragkb.config import load_env
from ragkb.spikes.capacity import run_capacity_spike
from ragkb.spikes.mineru import run_mineru_spike
from ragkb.spikes.models import run_model_spike
from ragkb.spikes.security import run_security_spike
from ragkb.spikes.zilliz import run_zilliz_spike


def test_all_harnesses_pass_without_claiming_real_acceptance() -> None:
    loaded = load_env()
    reports = [
        run_mineru_spike(
            loaded,
            loaded.repository_root / "backend/tests/fixtures/manifests/format-samples.yaml",
        ),
        run_zilliz_spike(loaded),
        run_model_spike(loaded),
        run_capacity_spike(loaded),
        run_security_spike(loaded),
    ]
    assert all(report["harness_passed"] for report in reports)
    assert all(report["real_acceptance"] is False for report in reports)
    assert all(report["blockers"] for report in reports)
