"""Run with the wheel environment's Python; uses a temporary working directory."""
import os
import subprocess
import sys
import tempfile


with tempfile.TemporaryDirectory(prefix='travelsync-wheel-check-') as directory:
    env = {k:v for k,v in os.environ.items() if k != 'PYTHONPATH'}
    code = '''
import hashlib, sys
from importlib.resources import files
from ourney_travelsync import load_places
from ourney_travelsync.places import OFFICIAL_FILENAME, OFFICIAL_SHA256
import ourney_travelsync
assert 'site-packages' in ourney_travelsync.__file__, ourney_travelsync.__file__
assert len(load_places().place_ids) == 3570
assert hashlib.sha256(files('ourney_travelsync').joinpath('data',OFFICIAL_FILENAME).read_bytes()).hexdigest() == OFFICIAL_SHA256
from ourney_travelsync.cli import main
assert main(['recommend','--top-k','2']) == 0
assert not any(name in sys.modules for name in ['requests','pandas','psycopg','streamlit'])
'''
    subprocess.run([sys.executable, '-I', '-c', code], cwd=directory, env=env, check=True)
