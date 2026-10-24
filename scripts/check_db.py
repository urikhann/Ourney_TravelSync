"""Compatibility wrapper; prefer ourney-travelsync db-check."""
import sys
from ourney_travelsync.cli import main

if __name__ == "__main__":
    sys.exit(main(["db-check", *sys.argv[1:]]))
