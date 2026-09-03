from ragkb.evaluation.uat_fresh_case_builder import build_fresh_case


def test_generated_fresh_case_excludes_old_answer():
    r = {
        "test_case_id": "t",
        "fixture_ref": "f",
        "source_sha256": "0" * 64,
        "locator": {"page": 1},
        "question": "q",
        "classification": "c",
        "render_proof": {},
    }
    out = build_fresh_case(r, "fresh")
    assert out["question"] == "q" and "answer" not in str(out)
