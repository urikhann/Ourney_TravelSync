# AGREE: future work, unimplemented

The previous package contained an entirely commented-out NumPy forward-pass
sketch with randomly initialized feature projections and POI-conditioned member
attention. It had no training loop, learned checkpoint or validated predictions.
It has been removed from the active package; Git history preserves the sketch.

Future work would require labeled group/individual interactions, a justified
training objective and negative sampling, group/user/time-aware evaluation splits,
checkpointing and comparison against the current AWM/GFAR baselines. No AGREE
recommendations or training capability are provided by this repository.
