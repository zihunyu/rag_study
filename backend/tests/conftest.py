"""Keep immutable external-evidence tests out of the hermetic unit-test job."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

ARTIFACT_BOUND_TEST_MODULES = frozenset(
    {
        "test_fixture_manifest_scan_cli.py",
        "test_fixture_render_scan.py",
        "test_local_sample_validation.py",
        "test_production_runtime_profile.py",
    }
)


def pytest_collection_modifyitems(items: Sequence[pytest.Item]) -> None:
    marker = pytest.mark.integration
    for item in items:
        if item.path.name in ARTIFACT_BOUND_TEST_MODULES:
            item.add_marker(marker)
