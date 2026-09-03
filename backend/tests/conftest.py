"""Keep immutable external-evidence tests out of the hermetic unit-test job."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

ARTIFACT_BOUND_TEST_MODULES = frozenset(
    {
        "test_docx_pdf_inputs.py",
        "test_embedding_remainder.py",
        "test_fixture_manifest_scan_cli.py",
        "test_fixture_render_scan.py",
        "test_local_sample_validation.py",
        "test_mineru_new_attempts.py",
        "test_production_runtime_profile.py",
        "test_real_format_validation.py",
        "test_real_uat.py",
        "test_uat_candidates.py",
        "test_uat_continuation_v3.py",
        "test_uat_systematic_revision_v4.py",
        "test_uat_systematic_revision_v5.py",
        "test_uat_systematic_v4_execution.py",
        "test_uat_systematic_v5_execution.py",
    }
)


def pytest_collection_modifyitems(items: Sequence[pytest.Item]) -> None:
    marker = pytest.mark.integration
    for item in items:
        if item.path.name in ARTIFACT_BOUND_TEST_MODULES:
            item.add_marker(marker)
