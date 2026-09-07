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


@pytest.fixture(autouse=True)
def isolated_unit_runtime(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's real .env must not turn temporary-file tests into production writes.

    Explicit integration tests retain their own configuration; individual profile
    contract tests may override these defaults with their existing monkeypatches.
    """
    if request.node.get_closest_marker("integration"):
        return
    for key, value in {
        "APP_ENV": "testing",
        "RAG_RUNTIME_PROFILE": "local",
        "VECTOR_BACKEND": "local",
        "AUTH_MODE": "local_single_user",
        "REAL_PROVIDER_CALLS_ENABLED": "false",
        "EXTERNAL_LIFECYCLE_MUTATIONS_ENABLED": "false",
        "OTEL_ENABLED": "false",
        "OCR_ENABLED": "false",
        "OCR_VERIFY_ENABLED": "false",
        "MODEL_ACCOUNT_LIMIT_ENABLED": "false",
        "MODEL_USAGE_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)


def pytest_collection_modifyitems(items: Sequence[pytest.Item]) -> None:
    marker = pytest.mark.integration
    for item in items:
        if item.path.name in ARTIFACT_BOUND_TEST_MODULES:
            item.add_marker(marker)
