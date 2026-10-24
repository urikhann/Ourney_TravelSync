import json
import pytest
from ourney_travelsync.ingestion.storage import validate_output_location
from ourney_travelsync import places


def test_generated_output_cannot_overlap_package():
    from pathlib import Path
    for path in (Path(places.__file__).parent, Path(places.__file__).parent/'data', Path(places.__file__).parents[1]):
        with pytest.raises(ValueError): validate_output_location(path)


def test_fixed_raw_processing(tmp_path):
    pytest.importorskip('pandas'); pytest.importorskip('requests')
    from ourney_travelsync.ingestion.processing import reprocess
    tour = tmp_path/'tour.json'; kakao = tmp_path/'kakao.json'
    tour.write_text(json.dumps([dict(contentid='1', title='Example Cafe', contenttypeid='39',
        cat1='A05',cat2='A0502',cat3='A05020900',addr1='\ubd80\uc0b0\uad11\uc5ed\uc2dc \ud574\uc6b4\ub300\uad6c',mapy='35.1',mapx='129.1')]))
    kakao.write_text(json.dumps([dict(id='2',place_name='Example Cafe',
        category_name='\uc74c\uc2dd\uc810 > \uce74\ud398',address_name='\ubd80\uc0b0\uad11\uc5ed\uc2dc \ud574\uc6b4\ub300\uad6c',y='35.1',x='129.1')]))
    report = reprocess(tour,kakao,tmp_path/'generated')
    assert report['rows'] == 1 and not report['activated']
    generated = tmp_path/'generated/travelsync_generated_places.csv'
    loaded = places.load_places(generated)
    assert loaded.records[0]['source'] == 'TOUR_API+KAKAO'
    assert loaded.records[0]['place_id'] == 'tour:1'
    assert loaded.records[0]['cafe'] == '1'
    again = reprocess(tour,kakao,tmp_path/'again')
    assert again == report
    assert generated.read_bytes() == (tmp_path/'again/travelsync_generated_places.csv').read_bytes()


def test_official_strict_import_validation():
    pytest.importorskip('psycopg')
    from importlib.resources import files, as_file
    from ourney_travelsync.db.poi_import import read_csv
    with as_file(files('ourney_travelsync').joinpath('data', places.OFFICIAL_FILENAME)) as path:
        rows = read_csv(path)
    assert len(rows) == 3570


def test_strict_csv_null_and_row_width(tmp_path):
    pytest.importorskip('psycopg')
    import csv
    from importlib.resources import files, as_file
    from ourney_travelsync.db.poi_import import read_csv, validate_rows
    with as_file(files('ourney_travelsync').joinpath('data', places.OFFICIAL_FILENAME)) as path:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            record = next(csv.DictReader(handle))
    path = tmp_path/'small.csv'
    record['phone'] = r'\N'
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(record)); writer.writeheader(); writer.writerow(record)
    rows = read_csv(path)
    assert rows[0]['phone'] is None
    assert validate_rows(rows)[0]['phone'] is None
    path.write_text(path.read_text(encoding='utf-8').rstrip()+',extra\n',encoding='utf-8')
    with pytest.raises(ValueError): read_csv(path)
