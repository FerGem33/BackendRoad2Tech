from scripts.forecast.metrics import empirical_coverage, pinball_loss, wape


def test_wape_and_quantile_metrics() -> None:
    assert wape([10, 20], [8, 18]) == 4 / 30
    assert pinball_loss([10], [8], 0.8) == 1.6
    assert empirical_coverage([10, 20], [15, 15]) == 0.5
