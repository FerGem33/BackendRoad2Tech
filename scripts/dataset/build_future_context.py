"""Create feature-only context for the 14 days immediately after observed sales.

This table deliberately has no sales or inventory target. It is the safe horizon that
the dashboard can pass to the demand model at demo time.
"""
import build_calendar_weather

from common import FORECAST_DATES, write_csv


def main() -> None:
    events = build_calendar_weather.build_events(FORECAST_DATES[0], FORECAST_DATES[-1])
    calendar = build_calendar_weather.build_calendar(events, FORECAST_DATES)
    weather = build_calendar_weather.build_weather(FORECAST_DATES, random_key=41)
    context = calendar.drop(columns=["demand_multiplier_hint"]).merge(weather, on="date", validate="one_to_one")
    write_csv(context, "forecast_context", float_format="%.1f")


if __name__ == "__main__":
    main()
