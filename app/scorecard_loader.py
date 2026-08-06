import os
import sys

if getattr(sys, "frozen", False):
    # Running as a PyInstaller-built exe: data files were bundled with
    # --add-data and unpacked to this temp dir at startup.
    _DATA_DIR = os.path.join(sys._MEIPASS, "data")
else:
    _DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

CRITERIA_PATH = os.path.join(_DATA_DIR, "criteria.csv")
RCM_PATH = os.path.join(_DATA_DIR, "rcm_violations.csv")
_VERSION_PATH = os.path.join(_DATA_DIR, "SCORECARD_VERSION")

with open(_VERSION_PATH, encoding="utf-8") as f:
    SCORECARD_VERSION = f.read().strip()
