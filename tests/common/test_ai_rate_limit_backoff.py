def test_rate_limit_retry_delay_uses_exponential_backoff():
    from investment_system.common.ai.ai_client import AIClient

    assert AIClient._rate_limit_retry_delay(10, 1) == 10
    assert AIClient._rate_limit_retry_delay(10, 2) == 20
    assert AIClient._rate_limit_retry_delay(10, 3) == 40


def test_rate_limit_retry_delay_is_capped():
    from investment_system.common.ai.ai_client import AIClient

    assert AIClient._rate_limit_retry_delay(30, 4, max_delay=60) == 60

