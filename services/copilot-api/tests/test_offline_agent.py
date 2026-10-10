from __future__ import annotations

import pytest

from stockcopilot.agent.offline import parse_intent


@pytest.mark.parametrize(
    ("text", "tool", "expected"),
    [
        ("Tendencia de Coca-Cola 600 ml en S016", "get_product_tendency", {"store": "S016", "product": "P001"}),
        (
            "Estrategia para reducir merma del sandwich de jamón, máximo 30% de descuento",
            "get_product_sell_strategy",
            {"product": "P059", "objective": "reduce_waste", "max_discount_pct": 30.0},
        ),
        ("¿Qué debo reabastecer hoy?", "get_restock_suggestions", {}),
        ("Compara la demanda de Tecate en todas las sucursales", "get_demand_by_store", {"product": "P023"}),
        ("Resumen de la tienda", "get_store_overview", {}),
        ("arma el plan de la semana", "generate_store_plan", {}),
    ],
)
def test_parse_intent(svc, text, tool, expected):
    parsed = parse_intent(text, svc.catalog, svc.as_of)
    assert parsed is not None
    name, args = parsed
    assert name == tool
    for key, value in expected.items():
        assert args[key] == value


def test_saturday_promotion_dates(svc):
    name, args = parse_intent("Quiero vender más Monster este sábado, puedo invertir $300", svc.catalog, svc.as_of)
    assert name == "get_product_sell_strategy"
    assert args["objective"] == "increase_sales"
    assert args["budget_mxn"] == 300.0
    assert args["start_date"] == "2026-09-19" and args["period_days"] == 1


def test_greeting_is_not_a_tool_call(svc):
    assert parse_intent("hola, buenos días", svc.catalog, svc.as_of) is None
