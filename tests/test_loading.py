import csv
import pytest
from ourney_travelsync import FEATURE_NAMES, load_places


def row():
    return dict(place_id='external:1', place_name='Example', category_name='Example',
                latitude='35.1', longitude='129.1', **dict.fromkeys(FEATURE_NAMES, '1'))


def write(path, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row()))
        writer.writeheader(); writer.writerows(rows)


def test_generic_loader_supports_other_sizes_and_empty(tmp_path):
    path = tmp_path/'custom.csv'
    for count in (0, 1, 4):
        write(path, [row() | {'place_id': str(i)} for i in range(count)])
        assert len(load_places(path).place_ids) == count


@pytest.mark.parametrize('change', [dict(food='0.2'), dict(latitude='inf'), dict(place_name=' '), dict(place_id='')])
def test_invalid_values(tmp_path, change):
    path = tmp_path/'bad.csv'; write(path, [row() | change])
    with pytest.raises(ValueError): load_places(path)


def test_ragged_and_duplicate_headers(tmp_path):
    path = tmp_path/'bad.csv'; write(path, [row()])
    text = path.read_text()
    for bad in (text.rstrip() + ',extra\n', text.splitlines()[0] + '\nx\n', text.replace('place_id,', 'place_name,')):
        path.write_text(bad)
        with pytest.raises(ValueError): load_places(path)
