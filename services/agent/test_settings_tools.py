from copy import deepcopy

import pytest

import brain
import trial_models
from test_providers import fixture_data, provider_environment, provider_for, response, wire_call


def data_and_item(fixture):
    data = trial_models.TrialData(fixture, fixture["cases"][0])
    item = data.get_plan()["plan"][0]["items"][0]
    return data, item


@pytest.mark.parametrize("tool,fields", [("set_visit_duration", {"duration_min": 75}), ("set_place_lock", {"locked": False})])
def test_settings_tool_requests_confirmation_without_writing(fixture_data, tool, fields):
    data, item = data_and_item(fixture_data)
    original = deepcopy(data.days)
    result = brain.run_tool(data, tool, {"item_id": item["item_id"], **fields})
    assert result["status"] == "CONFIRMATION_REQUIRED"
    assert result["pending_action"] == dict(action_type=tool, target_item_id=item["item_id"], constraints={}, **fields)
    assert data.days == original


@pytest.mark.parametrize("tool,fields", [("set_visit_duration", {"duration_min": 75}), ("set_place_lock", {"locked": True})])
def test_model_tool_call_returns_pending_action_to_backend(fixture_data, tool, fields):
    data, item = data_and_item(fixture_data)
    req = trial_models.trial_request(fixture_data["cases"][0], "en")
    provider = provider_for([response([wire_call(tool, {"item_id": item["item_id"], **fields})])])
    result = brain.model_turn(req, data, provider)
    assert result["proposal"] is None and result["needs_clarification"]
    assert result["pending_action"]["action_type"] == tool
    assert result["tool_calls"][-1]["name"] == tool


@pytest.mark.parametrize("fields", [{"duration_min": 0}, {"duration_min": 1441}, {"duration_min": True}])
def test_invalid_durations_are_not_settings_actions(fixture_data, fields):
    data, item = data_and_item(fixture_data)
    assert brain.run_tool(data, "set_visit_duration", {"item_id": item["item_id"], **fields}) == {"error": "invalid_visit_duration"}


@pytest.mark.parametrize("protection", [{"completed": True}, {"lock_reason": "confirmed_reservation"}])
def test_protected_tools_do_not_return_an_action(fixture_data, protection):
    data, item = data_and_item(fixture_data)
    data.protection = {item["item_id"]: protection}
    for tool, fields in [("set_place_lock", {"locked": False}), ("set_visit_duration", {"duration_min": 75})]:
        assert brain.run_tool(data, tool, {"item_id": item["item_id"], **fields}) == {"error": "place_lock_protected"}


def test_nonexistent_visit_cannot_be_changed(fixture_data):
    data, _ = data_and_item(fixture_data)
    assert brain.run_tool(data, "set_place_lock", {"item_id": "invented", "locked": False}) == {"error": "scheduled_visit_not_found"}


def test_get_plan_uses_actual_visit_duration_not_catalogue_default(fixture_data):
    data, item = data_and_item(fixture_data)
    data.days[0]["items"][0]["stay_minutes"] = 17
    assert data.get_plan()["plan"][0]["items"][0]["duration_min"] == 17
