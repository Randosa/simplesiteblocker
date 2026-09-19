"""Installed data stays beside the executable, never in the unpacked runtime."""
import os
from pathlib import Path
import sys

APP_DIR = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
           else Path(os.environ.get('ProgramW6432', os.environ.get('ProgramFiles', r'C:\Program Files'))) / 'SSB')
RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def resource(relative):
    return RESOURCE_DIR / relative
