"""Proposal cards resolve detailed POIs against the itinerary's pinned dataset."""
from .test_optimizer_integration import c
from .test_proposal_approval import planned_trip, swap_for, ask, replacement_fixture_clock


def test_proposal_exposes_pinned_dataset_for_detail_links(planning_adapter, replacement_fixture_clock):
    heads, tid, plan = planned_trip()
    _, _, proposal = swap_for(tid, plan)
    message = ask(heads[0], tid, proposal)
    assert message['agent']['proposal_status'] == 'PENDING', message
    version = message['agent']['dataset_version']
    assert version == plan['stats']['dataset_version']
    page = c.get(f'/trips/{tid}/chat', headers=heads[1]).json()
    saved = next(m for m in page['messages'] if m['id'] == message['id'])
    assert saved['agent']['dataset_version'] == version
    for item in saved['agent']['proposal']['add'] + saved['agent']['removed']:
        detail = c.get(f"/pois/{item['poi_id']}", params={'version': version}, headers=heads[1])
        assert detail.status_code == 200, detail.text
        assert detail.json()['category'] and detail.json()['name']
        assert detail.json()['dataset_version'] == version
    assert saved['agent']['votes']['approve'] == 0
