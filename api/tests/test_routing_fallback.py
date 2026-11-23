from .test_optimizer_integration import ready, generate


def test_quota_warning_is_retained_on_valid_saved_route_plan(planning_adapter,monkeypatch):
    from app import config
    monkeypatch.setattr(config,"OPTIMIZER_SEARCH_SECONDS",5)
    headers,tid,_=ready(individual_method="feature_mean")
    def limit_warning(req,response):
        response["routing_warnings"]=["routing_request_budget_exceeded"]
        return response
    planning_adapter.transform=limit_warning
    response=generate(headers,tid)
    assert response.status_code==200,response.text
    assert response.json()["plans"][0]["stats"]["routing_warnings"]==["routing_request_budget_exceeded"]

