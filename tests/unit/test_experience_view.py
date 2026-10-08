import pytest
from dataclasses import replace
from reproagent.experience import ExperienceSnapshot
from tests.unit.test_experience_store import card, exp, library


def view(*cards):
    return exp().ExperienceView(ExperienceSnapshot("ready", "a" * 64, cards))


def test_matching_is_bounded_and_stable():
    cards = tuple(card("advice " + str(i)) for i in range(4))
    result = view(*cards).select("pytest failed", ())
    assert len(result) == 3
    assert [r.id for r in result] == sorted(c.id for c in cards)[:3]
    assert view(*cards).select("completely unrelated", ()) == ()


def test_snapshot_does_not_follow_library_updates(tmp_path):
    path = tmp_path / "experiences.json"
    old = card(detail="original immutable hint")
    library(path, old)
    fixed = exp().ExperienceView(exp().load_experience_snapshot(path))
    summaries = fixed.select("pytest", ())
    library(path, card("other"))
    assert fixed.read(summaries[0].id)["detail"] == old.detail


def test_only_displayed_id_can_be_read():
    fixed = view(card())
    with pytest.raises(ValueError):
        fixed.read(card().id)
    summaries = fixed.select("pytest", ())
    with pytest.raises(ValueError):
        fixed.read("exp_unknown")
    assert fixed.read(summaries[0].id)["detail"] == card().detail


def test_one_successful_detail_per_task():
    fixed = view(card(), card("other"))
    summaries = fixed.select("pytest", ())
    fixed.read(summaries[0].id)
    with pytest.raises(ValueError):
        fixed.read(summaries[1].id)
    assert fixed.read_ids == (summaries[0].id,)
    assert fixed.select("other keywords", ()) == summaries


def test_utf8_visible_budget_is_shared():
    from reproagent.core.serialization import canonical_bytes
    large = card(summary="pytest " + "x" * 700, detail="y" * 500)
    fixed = view(large)
    summaries = fixed.select("pytest", ())
    assert summaries
    with pytest.raises(ValueError, match="budget"):
        fixed.read(large.id)
    assert fixed.read_ids == ()
    small = view(card())
    summaries = small.select("pytest", ())
    detail = small.read(summaries[0].id)
    assert len(canonical_bytes([{"id": x.id, "summary": x.summary} for x in summaries])) + len(canonical_bytes(detail)) <= 2048


def test_only_admitted_summaries_are_readable():
    fixed = view(card(), card("other"))
    summaries = fixed.select("pytest", ())
    fixed.restrict_summaries((summaries[0].id,))
    with pytest.raises(ValueError):
        fixed.read(summaries[1].id)
