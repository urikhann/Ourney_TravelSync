import pytest
from summarize_trials import summarize


def test_dry_runs_are_not_comparison_evidence():
    with pytest.raises(ValueError): summarize(dict(mode="dry_run",records=[{}]))


def test_failures_remain_in_denominator_and_empty_checks_do_not_pass():
    rows=[dict(model="m",language="en",status="answered",elapsed_seconds=1,checks={}),
          dict(model="m",language="ko",status="answered",elapsed_seconds=3,checks={"language":True}),
          dict(model="m",language="en",status="failed",error="rate limit")]
    result=summarize(dict(mode="live",records=rows,settings=dict(models=["m"]),fixture_sha256="fixture"))["summary"]["m"]
    assert result["answer_rate_all_planned"]==2/3
    assert result["pass_rate_all_planned"]==1/3
    assert result["successful_turn_median_seconds"]==2
    assert result["errors"]=={"rate limit":1}
