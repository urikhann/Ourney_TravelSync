from copy import deepcopy
from sqlalchemy import delete, select

from .test_optimizer_integration import ready, c, generate, configuration
from .test_flow import signup
from app.db import SessionLocal
from app.models import Poi, TripMember


def test_optional_choices_are_pinned_authorized_excluded_and_scheduled(planning_adapter,monkeypatch,request):
    from app import config
    monkeypatch.setattr(config,"OPTIMIZER_SEARCH_SECONDS",5)
    headers,tid,rec=ready(individual_method="feature_mean")
    with SessionLocal() as db:
        template=db.scalar(select(Poi).where(Poi.dataset_version==rec["dataset_version"]))
        values={column.name:deepcopy(getattr(template,column.name)) for column in Poi.__table__.columns}
        values.update(id="test-evening-pub",name="Test Evening Pub",category="음식점 > 술집 > 호프",tags=["alcohol"])
        db.add(Poi(**values));db.commit()
    def cleanup():
        with SessionLocal() as db:
            db.execute(delete(Poi).where(Poi.id=="test-evening-pub",Poi.dataset_version==rec["dataset_version"]))
            db.commit()
    request.addfinalizer(cleanup)
    url=f"/trips/{tid}/evening-choices"
    assert c.get(url).status_code in (401,403)
    assert c.get(url,headers=signup("EveningOutsider")).status_code==404
    rows=c.get(url,headers=headers).json()
    assert any(p["id"]=="test-evening-pub" for p in rows)
    body=configuration()
    body["days"][0]["end_min"]=1380
    body["locked_items"]=[dict(poi_id="test-evening-pub",date=body["days"][0]["date"])]
    response=generate(headers,tid,body)
    assert response.status_code==200,response.text
    visits=[v for d in response.json()["plans"][0]["days"] for v in d["items"]]
    pub=next(v for v in visits if v["poi_id"]=="test-evening-pub")
    assert pub["visit_category"]=="evening" and pub["start"]>="18:00"
    with SessionLocal() as db:
        member=db.scalar(select(TripMember).where(TripMember.trip_id==tid))
        member.exclusions={"tags":["alcohol"],"poi_ids":[]};db.commit()
    assert c.get(url,headers=headers).json()==[]
