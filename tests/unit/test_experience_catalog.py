from dataclasses import replace

import pytest

from reproagent.experience import ExperienceSnapshot
from reproagent.core.serialization import canonical_bytes
from tests.unit.test_experience_store import card


def view(cards=()):
    from reproagent.experience_catalog import ExperienceCatalogView
    return ExperienceCatalogView(ExperienceSnapshot('ready', 'a' * 64, tuple(cards)))


def test_catalog_lists_without_error_or_keyword_and_read_requires_display():
    fixed = view([card(detail='SECRET_DETAIL')])
    assert fixed.read(card().id)['code'] == 'not_displayed'
    page = fixed.list('model')
    assert page['code'] == 'ready'
    assert set(page['items'][0]) == {'id', 'category', 'name', 'description'}
    assert page['items'][0]['category'] == 'model'
    assert 'SECRET_DETAIL' not in str(page)
    assert fixed.read(card().id)['detail'] == 'SECRET_DETAIL'
    assert fixed.read(card().id)['code'] == 'read_limit'


def test_catalog_pages_are_complete_and_cursors_category_bound():
    cards = [card(str(i)) for i in range(20)]
    fixed = view(cards)
    first = fixed.list('model')
    assert len(first['items']) == 12
    assert fixed.list('workflow', first['next_cursor'])['code'] == 'invalid_cursor'
    second = fixed.list('model', first['next_cursor'])
    assert len(second['items']) == 8 and second['next_cursor'] is None
    assert [i['id'] for i in first['items'] + second['items']] == sorted(c.id for c in cards)
    assert view(cards).list('model', first['next_cursor'])['code'] == 'invalid_cursor'


def test_catalog_small_capacity_does_not_grant_read_or_skip():
    fixed = view([card()])
    assert fixed.list('model', max_bytes=32)['code'] == 'response_capacity'
    assert fixed.read(card().id)['code'] == 'not_displayed'
    assert fixed.list('model')['items'][0]['id'] == card().id


def test_catalog_success_and_detail_limits_are_task_owned():
    fixed = view([card()])
    for _ in range(6): assert fixed.list('workflow')['code'] == 'empty'
    assert fixed.list('model')['code'] == 'catalog_limit'
    assert fixed.read('not-a-card')['code'] == 'unknown_id'


def test_catalog_actual_utf8_byte_budget_and_whole_chinese_entries():
    cards = [replace(card(str(i)), name='入口定位', description='读取实现仍不能定位入口时加载。' * 8) for i in range(25)]
    fixed = view(cards)
    consumed = 0
    cursor = None
    for _ in range(6):
        result = fixed.list('model', cursor)
        if result['code'] not in ('ready', 'empty'):
            assert result['code'] in ('catalog_budget', 'catalog_limit')
            break
        consumed += len(canonical_bytes(result))
        assert consumed <= 8192 and len(canonical_bytes(result)) <= 4096
        assert all(item['description'] == cards[0].description for item in result['items'])
        cursor = result['next_cursor']
        if cursor is None: break
    assert fixed.catalog_bytes == consumed


def test_detail_capacity_failure_does_not_consume_read_allowance():
    fixed = view([card()])
    fixed.list('model')
    assert fixed.read(card().id, max_bytes=32)['code'] == 'response_capacity'
    assert fixed.read_ids == ()
    assert fixed.read(card().id)['detail']


def test_catalog_rejects_invalid_category_and_cursor_without_quota():
    fixed = view()
    assert fixed.list('../outside')['code'] == 'invalid_category'
    assert fixed.list('model', 'forged')['code'] == 'invalid_cursor'
    for _ in range(6): assert fixed.list('model')['code'] == 'empty'


def test_a_page_without_a_category_lists_every_card_under_its_own():
    """The categories say what a card is about, not who may read it.

    Requiring a category to see anything made a guess the gate: a phase that asked about
    workflow while the library held one framework card was told the library was empty.
    """
    cards = [card('one'), replace(card('two'), category='workflow')]

    page = view(cards).list()

    assert page['code'] == 'ready'
    assert len(page['items']) == 2
    assert {item['category'] for item in page['items']} == {'model', 'workflow'}
    # The narrowing still works, and an unknown category is still refused.
    assert len(view(cards).list('workflow')['items']) == 1
    assert view(cards).list('framework')['code'] == 'empty'
