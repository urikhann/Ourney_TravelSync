"""Byte preservation across Git checkout modes; requires no optional app dependencies."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location("verify_deployment_artifacts", ROOT / "scripts/verify_deployment_artifacts.py")
verifier = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(verifier)


def copy_artifacts(root):
    spec = json.loads((ROOT / verifier.SPEC).read_bytes())
    for relative in [Path(".gitattributes"), verifier.SPEC, *[verifier.DATA / name for name in spec["sha256"]]]:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    return spec


def test_deployment_artifacts_and_manifest_match():
    assert verifier.verify()["places"] == 3570


@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_git_index_and_checkout_preserve_snapshot_bytes(tmp_path, autocrlf):
    if not shutil.which("git"):
        pytest.skip("Git required for checkout regression")
    spec = copy_artifacts(tmp_path)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.STDOUT)
    git("init", "--quiet")
    git("config", "core.autocrlf", autocrlf)
    git("add", "--", ".gitattributes", verifier.DATA.as_posix())
    assert verifier.verify(tmp_path, git_ref="")["artifacts"] == 3
    # Force a new checkout from stored bytes, rather than re-reading the source copy.
    for name in spec["sha256"]:
        (tmp_path / verifier.DATA / name).write_bytes(b"changed in temporary test repository")
    git("checkout-index", "--all", "--force")
    assert verifier.verify(tmp_path, git_ref="")["places"] == 3570


def test_corrupt_artifact_is_rejected_with_its_name(tmp_path):
    spec = copy_artifacts(tmp_path)
    (tmp_path / verifier.DATA / spec["csv"]).write_bytes(b"corrupt catalogue")
    with pytest.raises(ValueError, match="hash mismatch: " + spec["csv"]):
        verifier.verify(tmp_path)
