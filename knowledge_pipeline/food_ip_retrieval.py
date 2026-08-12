"""Minimal, read-only KnowledgeCard retrieval for Question Tree v2.

This module deliberately selects stored knowledge only. It does not create
Owner Facts, infer a business objective, call an LLM, or write any artifact.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from food_ip_config import FOOD_IP_KNOWLEDGE_DIR, load_internal_methodology, load_question_tree
from food_ip_models import KnowledgeCard, QuestionEntry


# The 57-card snapshot was produced against Question Tree v1.1. This is an
# explicit compatibility lookup for those persisted IDs, not a card migration.
#
# The targets below are deliberately conservative. A legacy question is kept
# only where its stored meaning directly answers the v2 question; an empty
# tuple is preferable to presenting a merely adjacent card as an answer.
LEGACY_TO_V2_QUESTION_IDS: dict[str, tuple[str, ...]] = {
    "Q001": (),
    "Q002": (),
    "Q003": ("Q215",),
    "Q004": ("Q205", "Q209"),
    "Q010": ("Q201",),
    "Q011": ("Q201",),
    "Q013": ("Q203",),
    "Q020": ("Q209", "Q210"),
    "Q021": ("Q205",),
    "Q022": ("Q213", "Q224"),
    "Q030": ("Q224",),
    "Q031": ("Q209", "Q210", "Q212"),
    "Q032": ("Q204",),
    "Q041": ("Q215",),
    "Q050": ("Q216",),
    "Q051": ("Q216",),
    "Q052": ("Q216",),
    "Q053": ("Q216",),
    "Q070": ("Q228",),
    "Q071": ("Q207", "Q228"),
}


def _knowledge_cards_path(knowledge_dir: Path | str | None) -> Path:
    if knowledge_dir is not None:
        return Path(knowledge_dir) / "atomic" / "knowledge_cards.jsonl"
    root = Path(os.environ.get("FOOD_IP_KNOWLEDGE_DIR", str(FOOD_IP_KNOWLEDGE_DIR)))
    return root / "atomic" / "knowledge_cards.jsonl"


def _v2_question_ids() -> set[str]:
    try:
        return {
            QuestionEntry.model_validate(question).question_id
            for question in load_question_tree()
        }
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError("Question Tree v2 is invalid; retrieval cannot continue") from exc


def _load_knowledge_cards(cards_path: Path) -> list[KnowledgeCard]:
    try:
        with cards_path.open("r", encoding="utf-8") as handle:
            lines = list(enumerate(handle, start=1))
    except OSError as exc:
        raise ValueError(f"Cannot read KnowledgeCard snapshot: {cards_path}") from exc

    cards: list[KnowledgeCard] = []
    seen_knowledge_ids: set[str] = set()
    for line_number, raw_line in lines:
        if not raw_line.strip():
            raise ValueError(f"KnowledgeCard snapshot has a blank line at {line_number}")
        try:
            card = KnowledgeCard.model_validate(json.loads(raw_line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(
                f"Invalid KnowledgeCard at {cards_path}:{line_number}"
            ) from exc
        if card.knowledge_id in seen_knowledge_ids:
            raise ValueError(
                f"Duplicate KnowledgeCard knowledge_id {card.knowledge_id!r} "
                f"at {cards_path}:{line_number}"
            )
        seen_knowledge_ids.add(card.knowledge_id)
        cards.append(card)
    return cards


def retrieve_knowledge(
    question_id: str,
    *,
    max_cards: int = 3,
    knowledge_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Return at most ``max_cards`` v2-question-relevant KnowledgeCards.

    Cards are selected by their direct v2 ID or the explicit legacy mapping.
    The stable ranking is confidence descending, then deterministic knowledge ID.
    ``internal_methodology`` is intentionally a separate, opt-in result field.
    """
    if not 3 <= max_cards <= 5:
        raise ValueError("max_cards must be between 3 and 5")

    valid_question_ids = _v2_question_ids()
    if question_id not in valid_question_ids:
        raise ValueError(f"Unknown Question Tree v2 question_id: {question_id}")

    cards = _load_knowledge_cards(_knowledge_cards_path(knowledge_dir))
    selected = [
        card
        for card in cards
        if question_id in card.question_ids
        or any(question_id in LEGACY_TO_V2_QUESTION_IDS.get(qid, ()) for qid in card.question_ids)
    ]
    selected.sort(key=lambda card: (-card.confidence, card.knowledge_id))

    return {
        "question_id": question_id,
        "knowledge_cards": [
            card.model_dump(mode="json") for card in selected[:max_cards]
        ],
        "internal_methodology": load_internal_methodology(question_id),
    }
