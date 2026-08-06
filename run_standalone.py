"""
Standalone entry point for the PyInstaller-built .exe.

Not used by the Docker/docker-compose path — that still runs
`uvicorn app.main:app` directly, unchanged.

The DATABASE_URL default is set HERE rather than in app/db.py on purpose.
db.py deliberately has no default: on the Postgres deployment path, a missing
DATABASE_URL should stop the service from starting, not silently fall back to
a local file nobody knows about. Keeping the SQLite default in this file
confines it to the standalone build.
"""

import os
import sys

# Must run BEFORE `from app.main import app` — importing app.main pulls in
# app.db, which reads DATABASE_URL at module load time. If this line moves
# below the import, the exe goes back to raising KeyError on startup.
if getattr(sys, "frozen", False):
    # PyInstaller one-file build: put the DB next to the .exe, not in the
    # temp extraction dir, which is wiped on exit.
    _db_dir = os.path.dirname(sys.executable)
else:
    _db_dir = os.path.dirname(os.path.abspath(__file__))

_db_path = os.path.join(_db_dir, "aiscore_standalone.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path}")

import threading
import time
import webbrowser

import uvicorn

from app.main import app  # noqa: E402 — deliberate, see comment above

HOST = "127.0.0.1"
PORT = 8000


def _open_browser():
    time.sleep(1.5)  # give uvicorn a moment to bind the port
    webbrowser.open(f"http://{HOST}:{PORT}/docs")


if __name__ == "__main__":
    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"AISCORE running at http://{HOST}:{PORT}")
    print(f"Database: {_db_path}")
    print("Close this window to stop the server.")
    uvicorn.run(app, host=HOST, port=PORT)
