# webapp/main.py
#
# Run with: python -m webapp  (or: uvicorn webapp.app:app)

from __future__ import annotations

import uvicorn

from webapp.app import app

__all__ = ["app"]


def main() -> None:
    # 127.0.0.1 only, deliberately hardcoded — this tool spawns a real browser
    # tied to the desktop session and handles credentials in-process; there's
    # no reason for it to ever listen beyond localhost. See docs/backlog.md,
    # "Web GUI (webapp)" > Security.
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
