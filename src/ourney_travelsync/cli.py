"""Installed entry point; optional components are imported only when requested."""
import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path


def parser():
    p = argparse.ArgumentParser(description='TravelSync recommendations and versioned POI data')
    commands = p.add_subparsers(dest='command', required=True)
    c = commands.add_parser('e5-cache', help='Prewarm the pinned E5 POI cache before serving requests')
    c.add_argument('--csv')
    c.add_argument('--download', action='store_true', help='Explicitly allow downloading the pinned model')
    r = commands.add_parser('recommend', help='Recommend from the packaged v2 CSV or an external CSV')
    r.add_argument('--csv'); r.add_argument('--users', help='JSON object: user ID to ten named preferences')
    for name in ('recommend', 'db-recommend'):
        target = r if name == 'recommend' else commands.add_parser(name, help='Capture and persist a PostgreSQL recommendation run')
        target.add_argument('--algorithm', choices=['awm', 'gfar'], default='gfar')
        target.add_argument('--top-k', type=int, default=10)
        target.add_argument('--candidate-top-n', type=int, default=100)
        target.add_argument('--tie-seed', type=int, default=0)
        target.add_argument('--gfar-depth', type=int, default=20)
        target.add_argument('--misery-threshold', type=float)
        target.add_argument('--individual-method', choices=['binary', 'tfidf', 'feature_mean', 'e5_hybrid'], default='e5_hybrid')
        target.add_argument('--alpha', type=float, default=None)
        target.add_argument('--beta', type=float, default=None)
        target.add_argument('--diagnostics', action='store_true', help='Include scorer configuration and fallback details')
        target.add_argument('--preference-texts', help='JSON object mapping user IDs to explicit preference text')
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
    c = commands.add_parser('db-import-metadata', help='Validate and import the version-pinned metadata sidecar')
    c.add_argument('csv'); c.add_argument('--dataset-version'); c.add_argument('--validate-only', action='store_true')
    c = commands.add_parser('db-activate', help='Explicitly activate a reviewed READY dataset')
    c.add_argument('dataset_version'); c.add_argument('--expected-head')
    return p


def recommendation_config(a):
    from .common import validate_top_k
    from . import GFAR, AverageWithoutMisery
    validate_top_k(a.top_k); validate_top_k(a.candidate_top_n)
    from .recommenders.hybrid import ScoringConfig, SCORING_VERSION
    scoring = ScoringConfig.from_env(**{k: getattr(a, k) for k in ('alpha', 'beta') if getattr(a, k) is not None})
    method = a.individual_method
    if a.algorithm == 'awm':
        if method == 'tfidf':
            raise ValueError('AWM_REQUIRES_FEATURE_MEAN')
        method = 'feature_mean'
    threshold = a.misery_threshold if a.misery_threshold is not None else 2.5
    GFAR(a.gfar_depth); AverageWithoutMisery(threshold)
    if a.algorithm == 'awm' and not 1 <= threshold <= 5:
        raise ValueError('AWM_THRESHOLD_MUST_BE_BETWEEN_1_AND_5')
    texts = json.loads(Path(a.preference_texts).read_text(encoding='utf-8')) if a.preference_texts else {}
    if not isinstance(texts, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in texts.items()):
        raise ValueError('PREFERENCE_TEXTS_MUST_MAP_USER_IDS_TO_STRINGS')
    return dict(algorithm=a.algorithm, top_k=a.top_k, candidate_top_n=a.candidate_top_n,
                tie_seed=a.tie_seed, gfar_depth=a.gfar_depth, misery_threshold=threshold,
                algorithm_version=version('ourney-travelsync'), numpy_version=version('numpy'),
                tie_policy='sha256-identity-v1', individual_method=method, requested_scoring_method=a.individual_method,
                alpha=scoring.alpha, beta=scoring.beta,
                preference_texts=texts, scorer_version=SCORING_VERSION if method == 'e5_hybrid' else
                'method-c-char23-v1' if method == 'tfidf' else 'active-feature-mean-v1',
                sklearn_version=version('scikit-learn') if method == 'tfidf' else None,
                score_scale='relative_relevance_0_1' if method == 'e5_hybrid' else '0-1' if method == 'tfidf' else '1-5')


def dispatch(a):
    if a.command == 'e5-cache':
        from .places import load_places
        from .recommenders.hybrid import ScoringConfig, embeddings
        from .recommenders.hybrid import SemanticUnavailable
        try:
            _, _, metadata = embeddings(load_places(a.csv), ScoringConfig.from_env(local_files_only=not a.download, regenerate=True))
        except SemanticUnavailable as exc:
            raise ValueError(str(exc)) from None
        return metadata
    if a.command == 'recommend':
        from . import load_places, FEATURE_NAMES, GFAR, AverageWithoutMisery
        from .recommenders import prepare_group
        from .recommenders.hybrid import ScoringConfig
        config = recommendation_config(a)
        users = json.loads(Path(a.users).read_text(encoding='utf-8')) if a.users else {
            'alice': dict(zip(FEATURE_NAMES, [4,5,3,2,2,3,3,3,4,1])),
            'bob': dict(zip(FEATURE_NAMES, [3,2,5,4,3,2,4,2,5,3]))}
        if not isinstance(users, dict) or any(not isinstance(v, dict) for v in users.values()):
            raise ValueError('USERS_JSON_MUST_MAP_IDS_TO_NAMED_PREFERENCES')
        places = load_places(a.csv)
        model = GFAR(a.gfar_depth) if a.algorithm == 'gfar' else AverageWithoutMisery(config['misery_threshold'])
        group = prepare_group(places, users, individual_top_n=a.candidate_top_n,
                              tie_seed=a.tie_seed, individual_method=config['individual_method'],
                              scoring_config=ScoringConfig.from_env(alpha=config['alpha'], beta=config['beta']),
                              preference_texts=config['preference_texts'])
        results = model.recommend(group, top_k=a.top_k)
        metadata = {r['place_id']: r for r in places.records}
        rows = [dict(place_id=r.poi_id, place_name=metadata[r.poi_id]['place_name'],
                     **({'selection_gain': r.score} if a.algorithm == 'gfar' else {'mean_score': r.score})) for r in results]
        if a.diagnostics:
            return dict(group=rows, config=config, scoring=group.scoring.diagnostics if group.scoring else None,
                        status='ok' if rows else 'exhausted', exhaustion_reason=None if rows else 'no_candidates')
        return rows
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
        if a.command == 'db-import-metadata':
            from .db.metadata_import import import_metadata
            return import_metadata(conn, a.csv, a.dataset_version, validate_only=a.validate_only)
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
