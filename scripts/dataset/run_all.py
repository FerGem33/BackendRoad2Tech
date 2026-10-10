"""Regenerate the full v2 dataset, validate it and write its README."""
import sys

import build_calendar_weather
import build_campaigns
import build_catalogs
import build_future_context
import build_readme
import build_tickets
import simulate_operations
import validate

if __name__ == "__main__":
    build_catalogs.main()
    build_calendar_weather.main()
    build_campaigns.main()
    simulate_operations.main()
    build_future_context.main()
    build_tickets.main()
    status = validate.main()
    build_readme.main()
    sys.exit(status)
