"""Print v2 tie diagnostics with a reproducible default seed."""
import json
from ourney_travelsync.evaluation import candidate_tie_diagnostics

if __name__ == '__main__':
    print(json.dumps(candidate_tie_diagnostics(), indent=2))
