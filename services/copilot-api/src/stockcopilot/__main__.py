"""Run the API locally: `python -m stockcopilot` (or `uvicorn stockcopilot.api.main:app`)."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "stockcopilot.api.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "0") == "1",
    )


if __name__ == "__main__":
    main()
