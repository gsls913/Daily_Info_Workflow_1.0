from common_libs.runtime import daily_digest


def test_daily_claim_retries_until_digest_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "STATE_FILE", tmp_path / "daily_digest_state.json")

    assert daily_digest.claim_first_full_run_today() is True
    assert daily_digest.claim_first_full_run_today() is True

    daily_digest.mark_digest_result("failed", error="temporary failure")
    assert daily_digest.claim_first_full_run_today() is True

    daily_digest.mark_digest_result("generated", path="daily.md")
    assert daily_digest.claim_first_full_run_today() is False


def test_weekly_claim_retries_until_digest_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_digest, "STATE_FILE", tmp_path / "daily_digest_state.json")

    assert daily_digest.claim_first_full_run_this_week() is True
    assert daily_digest.claim_first_full_run_this_week() is True

    daily_digest.mark_weekly_digest_result("failed", error="temporary failure")
    assert daily_digest.claim_first_full_run_this_week() is True

    daily_digest.mark_weekly_digest_result("generated", path="weekly.md")
    assert daily_digest.claim_first_full_run_this_week() is False
