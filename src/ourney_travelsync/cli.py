"""Installed entry point; optional components are imported only when requested."""
import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path


def parser():
    p = argparse.ArgumentParser(description='TravelSync recommendations and versioned POI data')
    commands = p.add_subparsers(dest='command', required=True)
    r = commands.add_parser('recommend', help='Recommend from the packaged v2 CSV or an external CSV')
    r.add_argument('--csv'); r.add_argument('--users', help='JSON object: user ID to ten named preferences')
    for name in ('recommend', 'db-recommend'):
        target = r if name == 'recommend' else commands.add_parser(name, help='Capture and persist a PostgreSQL recommendation run')
        target.add_argument('--algorithm', choices=['awm', 'gfar'], default='gfar')
        target.add_argument('--top-k', type=int, default=10)
        target.add_argument('--candidate-top-n', type=int, default=100)
        target.add_argument('--tie-seed', type=int, default=0)
        target.add_argument('--gfar-depth', type=int, default=20)
        target.add_argument('--misery-threshold', type=float, default=2.5)
        if name == 'db-recommend':
            target.add_argument('group_id'); target.add_argument('constraint_id'); target.add_argument('--dataset-version')
    for name in ('collect', 'collect-to-db'):
        c = commands.add_parser(name, help='Live API collection; generated output is not the official snapshot')
        c.add_argument('--output', default='data/generated'); c.add_argument('--cache-dir')
        c.add_argument('--refresh', action='store_true'); c.add_argument('--validation-csv')
    c = commands.add_parser('reprocess', help='Offline processing of fixed raw API JSON lists')
    c.add_argument('tour_json'); c.add_argument('kakao_json'); c.add_argument('--output', default='data/generated/reprocessed')
    for name in ('db-init', 'db-migrate'):
        c = commands.add_parser(name, help='Explicit schema operation; use a disposable DB first')
        c.add_argument('--directory', default='database', help='Directory containing schema.sql and migrations/')
    commands.add_parser('db-check', help='Read dataset versions and active head')
    c = commands.add_parser('db-import', help='Strict CSV import; never activates a dataset')
    c.add_argument('csv'); c.add_argument('--validate-only', action='store_true')
    c = commands.add_parser('db-activate', help='Explicitly activate a reviewed READY dataset')
    c.add_argument('dataset_version'); c.add_argument('--expected-head')
    return p


def recommendation_config(a):
    from .common import validate_top_k
    from . import GFAR, AverageWithoutMisery
    validate_top_k(a.top_k); validate_top_k(a.candidate_top_n)
    GFAR(a.gfar_depth); AverageWithoutMisery(a.misery_threshold)
    return dict(algorithm=a.algorithm, top_k=a.top_k, candidate_top_n=a.candidate_top_n,
                tie_seed=a.tie_seed, gfar_depth=a.gfar_depth, misery_threshold=a.misery_threshold,
                algorithm_version=version('ourney-travelsync'), numpy_version=version('numpy'),
                tie_policy='sha256-identity-v1', score_scale='1-5')


def dispatch(a):
    if a.command == 'recommend':
        from . import load_places, FEATURE_NAMES, GFAR, AverageWithoutMisery
        from .recommenders import recommend_group
        recommendation_config(a)
        users = json.loads(Path(a.users).read_text(encoding='utf-8')) if a.users else {
            'alice': dict(zip(FEATURE_NAMES, [4,5,3,2,2,3,3,3,4,1])),
            'bob': dict(zip(FEATURE_NAMES, [3,2,5,4,3,2,4,2,5,3]))}
        if not isinstance(users, dict) or any(not isinstance(v, dict) for v in users.values()):
            raise ValueError('USERS_JSON_MUST_MAP_IDS_TO_NAMED_PREFERENCES')
        places = load_places(a.csv)
        model = GFAR(a.gfar_depth) if a.algorithm == 'gfar' else AverageWithoutMisery(a.misery_threshold)
        results = recommend_group(places, users, top_k=a.top_k, individual_top_n=a.candidate_top_n,
                                  tie_seed=a.tie_seed, recommender=model)
        metadata = {r['place_id']: r for r in places.records}
        return [dict(place_id=r.poi_id, place_name=metadata[r.poi_id]['place_name'],
                     **({'selection_gain': r.score} if a.algorithm == 'gfar' else {'mean_score': r.score})) for r in results]
    if a.command == 'reprocess':
        from .ingestion.processing import reprocess
        return reprocess(a.tour_json, a.kakao_json, a.output)
    if a.command.startswith('collect'):
        from .ingestion.travelsync_pipeline_rule_v2 import run
        result = run(a.output, a.cache_dir, a.refresh, a.validation_csv)
        if not result['report']['complete']:
            raise ValueError('PARTIAL_COLLECTION: saved for inspection; not imported')
        if a.command == 'collect':
            return dict(output=str(result['output']), rows=len(result['final_places']))
        from .db.connection import connect
        from .db.poi_import import save_pipeline_result
        with connect() as conn:
            return save_pipeline_result(conn, result, activate=False)
    if a.command in ('db-init', 'db-migrate'):
        from .db.operations import initialize, migrate
        return initialize(a.directory) if a.command == 'db-init' else migrate(a.directory)
    if a.command == 'db-import':
        from .db.poi_import import read_csv, import_rows
        rows = read_csv(a.csv)
        if a.validate_only:
            return dict(valid_rows=len(rows))
    from .db.connection import connect
    with connect() as conn:
        if a.command == 'db-import':
            return import_rows(conn, rows, activate=False)
        if a.command == 'db-activate':
            from .db.poi_import import activate_dataset
            activate_dataset(conn, a.dataset_version, a.expected_head)
            return dict(activated=a.dataset_version)
        if a.command == 'db-recommend':
            from .db.adapter import recommend_database
            run_id, results = recommend_database(conn, a.group_id, a.constraint_id,
                                                 recommendation_config(a), a.dataset_version)
            return dict(run_id=str(run_id), poi_ids=[r.poi_id for r in results])
        return conn.execute('SELECT d.*, (h.dataset_version IS NOT NULL) AS active FROM dataset_versions d LEFT JOIN dataset_head h USING(dataset_version) ORDER BY d.created_at').fetchall()


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        print(json.dumps(dispatch(a), ensure_ascii=False, default=str, indent=2))
        return 0
    except ModuleNotFoundError:
        extra = 'ingestion,postgres' if a.command == 'collect-to-db' else ('ingestion' if a.command in ('collect','reprocess') else 'postgres')
        print(f'Missing component dependency. Run: uv sync --extra {extra.replace(",", " --extra ")}', file=sys.stderr)
    except (ValueError, OSError, UnicodeError) as exc:
        print(str(exc) if isinstance(exc, ValueError) else 'FILE_READ_OR_WRITE_FAILED: check paths and permissions', file=sys.stderr)
    except Exception as exc:
        if type(exc).__module__.startswith('psycopg'):
            from .db.connection import safe_db_error
            print(safe_db_error(exc), file=sys.stderr)
        else:
            raise
    return 1


if __name__ == '__main__':
    sys.exit(main())
