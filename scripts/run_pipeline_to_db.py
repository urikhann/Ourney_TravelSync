"""Compatibility wrapper; prefer ourney-travelsync collect-to-db."""
import sys
from ourney_travelsync.cli import main

if __name__ == "__main__":
    sys.exit(main(["collect-to-db", *sys.argv[1:]]))
