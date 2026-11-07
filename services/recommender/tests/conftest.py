"""Make the HTTP service importable when tests run from the repository root."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
