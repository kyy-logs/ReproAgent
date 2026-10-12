from dataclasses import replace

import pytest

from reproagent import experience as exp
from reproagent.experience_triggers import ExperienceTrigger
from tests.unit.test_experience_store import card

RANGE = ExperienceTrigger("tool_error", "revise_contract", "SOURCE_RANGE_NOT_DISPLAYED")
HASH = ExperienceTrigger("tool_error", "revise_contract", "SOURCE_HASH_MISMATCH")


def triggered(summary="pytest source hint", detail="Use the returned source range.", condition=RANGE):
    old = card(summary, detail)
    return replace(old, id=exp._identifier(old.category, old.tags, old.summary, (condition,)), triggers=(condition,))


def view(*cards):
    return exp.ExperienceView(exp.ExperienceSnapshot("ready", "a"*64, cards))


def test_issue_words_cannot_preload_process_card():
    v = view(triggered())
    assert v.select("pytest source hint", ()) == ()
    assert [s.id for s in v.match_event(RANGE)] == [triggered().id]


def test_event_match_is_exact_and_not_capability_matching():
    v = view(triggered())
    v.select("logging", ())
    assert v.match_event(HASH) == ()
    assert v.match_event(ExperienceTrigger("tool_error", "Read", RANGE.code)) == ()
    assert v.match_event(ExperienceTrigger("tool_error", "revise_contract", "SOURCE")) == ()
    assert v.match_event(RANGE)


def test_new_summary_is_unreadable_until_actually_admitted():
    v = view(triggered())
    v.select("logging", ())
    selected = v.match_event(RANGE)
    with pytest.raises(ValueError):
        v.read(selected[0].id)
    v.admit_summaries((selected[0].id,))
    assert v.read(selected[0].id)["detail"] == triggered().detail
    assert v.match_event(RANGE) == ()


def test_initial_and_dynamic_summaries_share_total_count():
    initial = (card("pytest a"), card("pytest b"))
    first, second = triggered("range"), triggered("hash", condition=HASH)
    v = view(*initial, first, second)
    assert len(v.select("pytest", ())) == 2
    assert [s.id for s in v.match_event(RANGE)] == [first.id]
    v.admit_summaries((first.id,))
    assert v.match_event(HASH) == ()
    assert v.last_match_code == "count_limit"


def test_detail_bytes_are_retained_across_dynamic_events():
    v = view(card("pytest", "d"*1750), triggered("r"*400))
    initial = v.select("pytest", ())
    v.read(initial[0].id)
    assert v.match_event(RANGE) == ()
    assert v.last_match_code == "visible_budget"


def test_dropped_dynamic_card_is_not_readable_or_counted():
    v = view(triggered())
    v.select("logging", ())
    selected = v.match_event(RANGE)
    v.admit_summaries(())
    with pytest.raises(ValueError):
        v.read(selected[0].id)
    assert v.match_event(RANGE)


def test_large_card_does_not_discard_small_relevant_card():
    small, large = triggered("small"), triggered("x"*3000)
    v = view(large, small)
    v.select("logging", ())
    assert [s.id for s in v.match_event(RANGE)] == [small.id]


def test_pending_summary_and_detail_cannot_exceed_shared_budget():
    v = view(card('pytest','d'*1600), triggered('r'*500))
    initial = v.select('pytest',())
    proposed = v.match_event(RANGE)
    assert proposed
    with pytest.raises(ValueError,match='budget'):
        v.read(initial[0].id)
    assert v.read_ids == ()
    v.admit_summaries((proposed[0].id,))
