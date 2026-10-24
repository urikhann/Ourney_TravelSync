"""Compatibility wrapper; prefer ourney-travelsync db-activate."""
import sys
from ourney_travelsync.cli import main

if __name__ == "__main__":
    sys.exit(main(["db-activate", *sys.argv[1:]]))
