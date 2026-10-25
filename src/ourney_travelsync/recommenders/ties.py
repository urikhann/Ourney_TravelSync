"""Identity-based tie ordering, independent of source row order."""
import hashlib
import numpy as np


def tie_keys(ids, seed=0):
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("tie_seed must be an integer.")
    return [hashlib.sha256(f"{seed}\0{identity}".encode()).hexdigest() for identity in ids]


def ranking(scores, ids, seed=0):
    """Descending score, then seeded SHA256 of stable identity, then identity."""
    keys = tie_keys(ids, seed)
    return np.asarray(sorted(range(len(ids)), key=lambda i: (-float(scores[i]), keys[i], ids[i])), dtype=int)
