from ragkb.evaluation.uat_confirmation_join import join_confirmation


def test_generated_join_uses_index_and_bundle_hash_only():
    rows = [{"review_index": 1, "source_bundle_sha256_from_case": "h"}]
    cases = [
        {
            "review_index": 1,
            "source_bundle_sha256": "h",
            "test_case_id": "t",
            "fixture_ref": "f",
            "category": "c",
            "locator": {},
            "source_sha256": "s",
        }
    ]
    assert join_confirmation(rows, cases)[0]["test_case_id"] == "t"
