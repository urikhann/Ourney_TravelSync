"""Compatibility wrapper; prefer ourney-travelsync db-import."""
import sys
from ourney_travelsync.cli import main

if __name__ == "__main__":
    sys.exit(main(["db-import", *sys.argv[1:]]))
