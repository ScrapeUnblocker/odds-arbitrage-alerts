import json

import pytest

from odds_arbitrage_alerts.state import StateError, dump_state, load_state, save_state

STATE = {
    "prices": {
        "1|Match Result|Draw|": {"odds": 3.75, "since": "2026-11-09T13:05Z", "seen": "2026-11-09"}
    },
    "arbs": {},
}


def test_missing_file_is_empty_state(tmp_path):
    assert load_state(tmp_path / "state.json") == {"prices": {}, "arbs": {}}


def test_round_trip_and_unchanged_detection(tmp_path):
    path = tmp_path / "state" / "odds.json"
    assert save_state(path, STATE) is True
    assert load_state(path) == STATE
    assert save_state(path, STATE) is False
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    assert not (path.parent / "odds.json.tmp").exists()


def test_dump_is_sorted_and_stable():
    a = dump_state({"prices": {"b": {}, "a": {}}, "arbs": {}})
    b = dump_state({"arbs": {}, "prices": {"a": {}, "b": {}}})
    assert a == b
    assert a.index('"a"') < a.index('"b"')


@pytest.mark.parametrize("content", ["not json", "[]", '{"prices": []}'])
def test_unreadable_state(tmp_path, content):
    path = tmp_path / "odds.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(StateError):
        load_state(path)
