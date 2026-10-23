"""跨会话角色库：一张卡一个角色，库内可存任意多张。"""

from __future__ import annotations

import json

from novel_agent.core.models import CharacterCard
from novel_agent.settings import Settings


def _path(settings: Settings):
    return settings.novelagent_home / "character_library.json"


def list_cards(settings: Settings) -> list[CharacterCard]:
    path = _path(settings)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    cards = []
    for item in raw if isinstance(raw, list) else []:
        try:
            cards.append(CharacterCard.model_validate(item))
        except Exception:
            continue
    return cards


def upsert_card(settings: Settings, card: CharacterCard) -> list[CharacterCard]:
    cards = list_cards(settings)
    replaced = False
    for i, old in enumerate(cards):
        if old.char_id == card.char_id:
            cards[i] = card
            replaced = True
            break
    if not replaced:
        cards.append(card)
    _path(settings).write_text(
        json.dumps([c.model_dump() for c in cards], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return cards


def delete_card(settings: Settings, char_id: str) -> list[CharacterCard]:
    cards = [c for c in list_cards(settings) if c.char_id != char_id]
    _path(settings).write_text(
        json.dumps([c.model_dump() for c in cards], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return cards
