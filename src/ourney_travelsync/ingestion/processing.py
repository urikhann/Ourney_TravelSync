"""Reprocess fixed JSON inputs without API calls or database writes."""
import hashlib
import json
from pathlib import Path
from .storage import validate_output_location


def reprocess(tour_json, kakao_json, output):
    from .travelsync_pipeline_rule_v2 import prepare
    root = validate_output_location(output)
    paths = [Path(tour_json), Path(kakao_json)]
    raw = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
    if any(not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) for rows in raw):
        raise ValueError('RAW_JSON_MUST_CONTAIN_LISTS_OF_OBJECTS')
    frames = prepare(*raw)
    root.mkdir(parents=True, exist_ok=True)
    frames[3].to_csv(root/'travelsync_generated_places.csv', index=False, encoding='utf-8-sig')
    frames[4].to_csv(root/'duplicate_audit.csv', index=False, encoding='utf-8-sig')
    report = dict(rows=len(frames[3]), input_sha256=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths],
                  activated=False, live_collection=False)
    (root/'processing_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
