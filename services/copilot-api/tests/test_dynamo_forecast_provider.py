from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stockcopilot.domain.forecast_provider import DynamoForecastProvider, ForecastUnavailableError


class FakeTable:
    def __init__(self, item):
        self.item = item
        self.last_key = None

    def get_item(self, *, Key):
        self.last_key = Key
        return {"Item": self.item} if self.item else {}


def provider(item):
    instance = object.__new__(DynamoForecastProvider)
    instance._table = FakeTable(item)
    return instance


def record():
    return {
        "model_version": "3",
        "backtest_wape_p50": Decimal("0.123"),
        "forecast": [
            {"date": "2026-09-17", "p50": Decimal("10"), "p80": Decimal("12"), "p90": Decimal("15")},
            {"date": "2026-09-18", "p50": Decimal("11"), "p80": Decimal("13"), "p90": Decimal("16")},
        ],
    }


def test_dynamo_provider_maps_a_validated_batch_record():
    svc = provider(record())
    result = svc.forecast("S001", "P001", date(2026, 9, 16), 2)
    assert result.model == "sagemaker-catboost-3"
    assert result.dates == [date(2026, 9, 17), date(2026, 9, 18)]
    assert result.p50.tolist() == [10.0, 11.0]
    assert result.backtest_wape == 0.123
    assert svc._table.last_key == {"pk": "FORECAST#S001#P001", "sk": "AS_OF#2026-09-16"}


def test_dynamo_provider_rejects_missing_and_invalid_forecasts():
    with pytest.raises(ForecastUnavailableError, match="No SageMaker forecast"):
        provider(None).forecast("S001", "P001", date(2026, 9, 16), 1)
    with pytest.raises(ValueError, match="1 through 14"):
        provider(record()).forecast("S001", "P001", date(2026, 9, 16), 15)
    broken = record()
    broken["forecast"][0]["p80"] = Decimal("9")
    with pytest.raises(ForecastUnavailableError, match="quantiles"):
        provider(broken).forecast("S001", "P001", date(2026, 9, 16), 1)
