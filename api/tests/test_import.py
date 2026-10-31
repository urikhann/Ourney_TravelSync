"""CSV import: all-or-nothing, versions never change, only one ACTIVE version."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.features import FEATURES
from app.import_places import ImportProblem, check_rows, import_rows
from app.models import DatasetVersion, Poi

REAL_CSV = Path(__file__).resolve().parents[2] / "src" / "ourney_travelsync" / "data" / "travelsync_poi_metadata_enriched_v1.csv"
COLS = ["place_id", "place_name", "category_name", "address", "latitude", "longitude", "source", *FEATURES]


def row(pid, name="장소", feats=("cafe",), lat="35.15", cat="음식점 > 카페"):
    return {"place_id": pid, "place_name": name, "category_name": cat, "address": "부산광역시 해운대구 우동 1",
            "latitude": lat, "longitude": "129.1", "source": "KAKAO", **{f: "1" if f in feats else "0" for f in FEATURES}}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_import_activates_and_archives(db):
    import_rows(db, [row("kakao:1"), row("kakao:2", "금정산", ("nature",), cat="관광지")], COLS, "v1", "rule_v2")
    import_rows(db, [row("kakao:1")], COLS, "v2", "rule_v2")
    status = dict(db.execute(select(DatasetVersion.version, DatasetVersion.status)).all())
    assert status == {"v1": "ARCHIVED", "v2": "ACTIVE"}
    old = db.get(Poi, ("kakao:2", "v1"))
    assert old is not None and old.tags == ["hiking"] and old.area == "해운대구"   # old version is kept
    p = db.get(Poi, ("kakao:1", "v2"))
    assert p.avg_cost_krw is None and p.field_status["cost"] == "UNKNOWN"         # unknown is not 0 won


@pytest.mark.parametrize("bad", ["2", "0.5", "-1", "", "1.0"])
def test_bad_feature_value_refuses_whole_file(db, bad):
    r = row("kakao:9")
    r["food"] = bad
    with pytest.raises(ImportProblem):
        import_rows(db, [row("kakao:8"), r], COLS, "v1", "rule_v2")
    assert db.scalar(select(DatasetVersion)) is None and db.scalar(select(Poi)) is None


def test_other_checks(db):
    probs = check_rows([row("a"), row("a"), row("b", feats=()), row("c", lat="37.5"), row("d", name=" ")], COLS)
    text = " ".join(probs)
    assert "duplicate place_id a" in text and "all 10 features are 0" in text
    assert "outside Busan" in text and "empty place_name" in text
    with pytest.raises(ImportProblem):
        import_rows(db, [row("a")], COLS[:-1], "v1", "rule_v2")   # a feature column is missing


def test_versions_are_never_reused(db):
    import_rows(db, [row("a")], COLS, "v1", "rule_v2")
    with pytest.raises(ImportProblem):
        import_rows(db, [row("b")], COLS, "v1", "rule_v2")


@pytest.mark.skipif(not REAL_CSV.exists(), reason="data file not present")
def test_the_real_pipeline_csv_passes_the_checks():
    import csv
    with open(REAL_CSV, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert len(rows) == 3570
    assert check_rows(rows, reader.fieldnames) == []


def test_enriched_csv_metadata_survives_database_and_response(db):
    import csv
    from app.import_places import CORE_COLUMNS, import_csv
    from app.places import place_dict

    with REAL_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert import_csv(db, REAL_CSV, "enriched-test") == 3570
    expected = {k: v for k, v in rows[0].items() if k not in CORE_COLUMNS}
    db.expire_all()
    saved = db.get(Poi, (rows[0]["place_id"], "enriched-test"))
    assert len(expected) == 43
    assert saved.enriched_metadata == expected
    assert place_dict(saved)["enriched_metadata"] == expected
