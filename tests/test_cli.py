import json
import os
import subprocess
import sys


def run(*args):
    return subprocess.run([sys.executable, '-m', 'ourney_travelsync.cli', *args], capture_output=True, text=True, encoding='utf-8',
                          env=os.environ | {'PYTHONIOENCODING':'utf-8'})


def test_help_and_packaged_recommendation():
    assert run('--help').returncode == 0
    for algorithm in ('awm','gfar'):
        process = run('recommend', '--top-k', '3', '--algorithm', algorithm)
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert len(result) == 3 and all('place_name' in r for r in result)


def test_cli_actionable_errors(tmp_path):
    assert run('recommend', '--top-k', '-1').returncode == 1
    bad = tmp_path/'bad.json'; bad.write_text('[]')
    assert 'USERS_JSON' in run('recommend', '--users', str(bad)).stderr
