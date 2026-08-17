from eval.runner import run_all


def test_all_evaluation_fixtures_pass() -> None:
    results = run_all()
    assert len(results) == 8
    assert all(result["passed"] for result in results), results
