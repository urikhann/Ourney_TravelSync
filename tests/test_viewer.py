import pytest
from pathlib import Path


def test_empty_and_populated_viewer_without_database(monkeypatch):
    pytest.importorskip('streamlit'); pytest.importorskip('psycopg')
    from streamlit.testing.v1 import AppTest
    from ourney_travelsync.db import connection, repository
    from ourney_travelsync.features import FEATURE_NAMES
    from contextlib import nullcontext
    monkeypatch.setattr(connection, 'connect', lambda: nullcontext(object()))
    monkeypatch.setattr(repository, 'get_active_pois', lambda conn: [])
    app = AppTest.from_file(Path(__file__).resolve().parents[1]/'apps/data_viewer.py').run()
    assert not app.exception
    assert len(app.info) == 1
    # Refresh explicitly to clear the cache populated by the empty-data case.
    monkeypatch.setattr(repository, 'get_active_pois', lambda conn: [dict(place_name='Example',
        category_name='cafe',address='Busan',source='KAKAO',**dict.fromkeys(FEATURE_NAMES,1))])
    app.button[0].click().run()
    assert not app.exception
    assert app.metric[0].value == '1'
