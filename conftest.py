"""Pytest root conftest: put the repo root on sys.path so tests import top-level
modules (config, providers, core, retrieve.*) without needing PYTHONPATH or an install."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
