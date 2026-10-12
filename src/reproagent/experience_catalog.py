"""Compact advisory catalog metadata; never current source evidence."""
from dataclasses import dataclass


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
