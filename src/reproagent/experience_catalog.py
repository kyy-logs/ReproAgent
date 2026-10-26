"""Compact advisory catalog metadata; never current source evidence."""
from dataclasses import dataclass, asdict
from secrets import token_hex

from .core.models import ExperienceCatalogNotice
from .core.serialization import canonical_bytes

CATALOG_BYTES = 16384
PAGE_BYTES = 8192
DETAIL_BYTES = 8192
CATALOG_CALLS = 12
PAGE_ITEMS = 24

#: How many card details one task may load.  Two is enough to weigh a first candidate
#: against a second without turning browsing into a substitute for exploring.
DETAIL_READS = 2


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    name: str
    description: str


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + '…'


def entry_from_card(card) -> CatalogEntry:
    return CatalogEntry(card.id, card.name or _short(card.summary, 64),
                        card.description or _short(card.summary, 160))


class ExperienceCatalogView:
    """One frozen task catalog. Successful complete pages authorize detail reads."""
    def __init__(self, snapshot, *, secrets=()):
        self.snapshot = snapshot
        self.secrets = tuple(s for s in secrets if s)
        self._cards = {card.id: card for card in snapshot.cards}
        self._shown = set()
        self._cursors = {}
        self._read_ids = ()
        self.catalog_bytes = 0
        self.catalog_calls = 0

    @property
    def read_ids(self):
        return self._read_ids

    def _safe(self, text):
        for secret in self.secrets: text = text.replace(secret, '[REDACTED]')
        return text

    def list(self, category: str | None = None, cursor: str | None = None, *, max_bytes: int = PAGE_BYTES) -> dict:
        from .experience import CATEGORIES
        def result(code, items=(), next_cursor=None):
            return {'code': code, 'category': category if category in CATEGORIES else '',
                    'items': list(items), 'next_cursor': next_cursor}
        if category is not None and category not in CATEGORIES: return result('invalid_category')
        start = 0
        if cursor is not None:
            location = self._cursors.get(cursor) if type(cursor) is str else None
            if location is None or location[0] != category: return result('invalid_cursor')
            start = location[1]
        if self.catalog_calls >= CATALOG_CALLS: return result('catalog_limit')
        remaining = CATALOG_BYTES - self.catalog_bytes
        cap = min(PAGE_BYTES, max_bytes, remaining)
        page = self._page(category, start, cap)
        if page is None:
            return result('catalog_budget' if remaining < min(PAGE_BYTES, max_bytes) else 'response_capacity')
        if page['next_cursor'] is not None:
            cursor = token_hex(16)
            page['next_cursor'] = cursor
            self._cursors[cursor] = (category, start + len(page['items']))
        self._shown.update(item['id'] for item in page['items'])
        self.catalog_calls += 1
        self.catalog_bytes += len(canonical_bytes(page))
        return page

    def _page(self, category, start, cap):
        """Pure page projection shared by actual listing and hint availability."""
        def result(code, items=(), next_cursor=None):
            return {'code': code, 'category': category or '', 'items': list(items), 'next_cursor': next_cursor}
        cards = sorted((c for c in self._cards.values() if category is None or c.category == category),
                       key=lambda c: c.id)
        items, next_cursor = [], None
        code = 'ready' if cards[start:] else 'empty'
        for position, card in enumerate(cards[start:start + PAGE_ITEMS], start=start):
            entry = asdict(entry_from_card(card))
            entry.update(name=self._safe(entry['name']), description=self._safe(entry['description']),
                         category=card.category)
            proposed_cursor = '0' * 32 if position + 1 < len(cards) else None
            draft = result(code, (*items, entry), proposed_cursor)
            if len(canonical_bytes(draft)) > cap: break
            items.append(entry)
            next_cursor = proposed_cursor
        page = result(code, items, next_cursor)
        if (cards[start:] and not items) or len(canonical_bytes(page)) > cap:
            return None
        return page

    def notice(self) -> ExperienceCatalogNotice | None:
        """What a phase is told up front: that there is history, and nothing it says."""
        if not self._cards:
            return None
        return ExperienceCatalogNotice(len(self._cards),
            tuple(sorted({card.category for card in self._cards.values()})),
            tuple((category, sum(c.category == category for c in self._cards.values()))
                  for category in ('framework', 'model', 'workflow')))

    def hint_unavailable_reason(self, *, max_bytes: int = PAGE_BYTES) -> str | None:
        """Inspect remaining capacity without authorizing IDs or spending a read."""
        if self.catalog_calls >= CATALOG_CALLS:
            return 'catalog_limit'
        if len(self._read_ids) >= DETAIL_READS:
            return 'read_limit'
        remaining = CATALOG_BYTES - self.catalog_bytes
        cap = min(PAGE_BYTES, max_bytes, remaining)
        # Do not skip an undeliverable leading entry to find a later short card.
        # Only IDs reachable through a real first page can support this hint.
        for category in sorted({card.category for card in self._cards.values()}):
            page = self._page(category, 0, cap)
            if page is not None and any(len(canonical_bytes(self._detail(self._cards[item['id']])))
                    <= min(DETAIL_BYTES, max_bytes) for item in page['items']):
                return None
        return 'catalog_budget' if remaining < min(PAGE_BYTES, max_bytes) else 'response_capacity'

    def _detail(self, card):
        entry = entry_from_card(card)
        return {'id': card.id, 'name': self._safe(entry.name), 'description': self._safe(entry.description),
                'summary': self._safe(card.summary), 'detail': self._safe(card.detail)}

    def read(self, identifier: str, *, max_bytes: int = DETAIL_BYTES) -> dict:
        if len(self._read_ids) >= DETAIL_READS: return {'code': 'read_limit'}
        card = self._cards.get(identifier)
        if card is None: return {'code': 'unknown_id'}
        if identifier not in self._shown: return {'code': 'not_displayed'}
        data = self._detail(card)
        if len(canonical_bytes(data)) > min(DETAIL_BYTES, max_bytes): return {'code': 'response_capacity'}
        self._read_ids = (*self._read_ids, identifier)
        return data
