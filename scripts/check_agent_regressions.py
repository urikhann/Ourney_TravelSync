"""Run agent-related regressions without resetting repository databases."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = Path(
    "src/ourney_travelsync/data/"
    "travelsync_poi_metadata_enriched_v3.csv"
)


def run_suite(name, paths, *, cwd, env, work):
    """Run one suite in a fresh Python process and retain its output."""

    print(f"\nRunning {name} tests...", flush=True)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(cwd),
            "--confcutdir",
            str(cwd),
            "--basetemp",
            str(work / f"pytest-{name}"),
            *paths,
        ],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    (work / f"{name}.log").write_text(
        result.stdout,
        encoding="utf-8",
    )
    print(result.stdout, flush=True)

    return result.returncode


def main():
    work = Path(tempfile.mkdtemp(prefix="ts-agent-regression-"))
    print(f"Temporary test copy and logs: {work}", flush=True)

    ignored = shutil.ignore_patterns(
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
        ".env*",
        "*.db",
        "*.db-*",
    )

    for relative in ("api/app", "api/tests", "services/mocks"):
        destination = work / relative
        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copytree(
            ROOT / relative,
            destination,
            ignore=ignored,
        )

    copied_csv = work / CSV_PATH
    copied_csv.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / CSV_PATH, copied_csv)

    base_env = os.environ.copy()
    base_env.update({
        "DATABASE_URL": "sqlite://",
        "SECRET_KEY": "agent-regression-tests-only-secret-at-least-32-chars",
        "PLACES_CSV": "",
        "HOURS_CSV": "",
        "PLACES_FOLLOW_ENV": "false",
        "FEATURE_AGENT": "false",
        "FEATURE_AVAILABILITY": "false",
        "SMTP_HOST": "",
        "TRAVELSYNC_TEST_DATABASE_URL": "",
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })

    for setting in (
        "RECOMMENDER_URL",
        "OPTIMIZER_URL",
        "AVAILABILITY_URL",
        "AGENT_URL",
    ):
        base_env[setting] = "http://localhost:8001"

    api_result = run_suite(
        "api",
        ["api/tests"],
        cwd=work,
        env=base_env,
        work=work,
    )

    recommender_env = base_env.copy()
    recommender_env.update({
        "PLACES_CSV": str(ROOT / CSV_PATH),
        "DATASET_VERSION": "busan-enriched-v3",
        "GFAR_DEPTH": "20",
        "AWM_THRESHOLD": "2.5",
    })

    recommender_result = run_suite(
        "recommender",
        ["tests"],
        cwd=ROOT / "services/recommender",
        env=recommender_env,
        work=work,
    )

    package_result = run_suite(
        "package",
        ["tests", "integration_tests"],
        cwd=ROOT,
        env=base_env,
        work=work,
    )

    results = {
        "api": api_result,
        "recommender": recommender_result,
        "package": package_result,
    }

    print(f"\nLogs retained at: {work}")

    if any(code != 0 for code in results.values()):
        print("Some checks failed. Review the corresponding log.")
        return 1

    print("All regression commands completed successfully.")
    print("Check the summaries for any skipped tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

