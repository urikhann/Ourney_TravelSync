"""Compatibility wrapper; prefer ourney-travelsync db-init."""
import sys
from ourney_travelsync.cli import main

if __name__ == "__main__":
    sys.exit(main(["db-init", *sys.argv[1:]]))
