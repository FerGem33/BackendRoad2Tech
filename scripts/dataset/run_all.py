"""Regenerate the full v2 dataset, validate it and write its README."""
import sys

import build_calendar_weather
import build_catalogs
import build_readme
import simulate_operations
import validate

if __name__ == "__main__":
    build_catalogs.main()
    build_calendar_weather.main()
    simulate_operations.main()
    status = validate.main()
    build_readme.main()
    sys.exit(status)
