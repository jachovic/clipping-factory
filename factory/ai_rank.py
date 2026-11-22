"""Optional AI re-ranking of clip candidates with Claude (enabled when ANTHROPIC_API_KEY is set).

The heuristic detector proposes candidates; Claude scores each one for standalone value, hook strength,
clarity and brief compliance, and suggests a short on-screen hook that is faithful to what is said.
Cost is checked against the spend limits before the call and booked to the ledger after it."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from .config import secret_present
from .models import Campaign
from .ops import SpendLimitExceeded, audit, check_spend, record_cost

log = logging.getLogger("factory.ai_rank")

MODEL = "claude-opus-5-5"
USD_PER_INPUT_TOKEN = 4.00 / 1_000_000
USD_PER_OUTPUT_TOKEN = 20.00 / 1_000_000
EST_CALL_USD = 0.15            # conservative pre-call estimate for the spend guard
MAX_CANDIDATES = 12

SYSTEM = (
    "You evaluate excerpts from long-form video transcripts as candidates for short vertical clips "
    "(TikTok, Reels, Shorts) made for a paid clipping campaign. Score each excerpt 0-100 for how well it works "
    "as a standalone clip: a strong first sentence, a complete thought, clarity without outside context, and "
    "fit with the campaign brief. Give 0 to excerpts that break the brief, misrepresent the speaker when cut "
    "out of context, or depend mainly on provocation. For each excerpt also write an on-screen hook of at most "
    "60 characters that is faithful to what is actually said - no invented claims, no clickbait the excerpt "
    "does not support. Keep reasons to one short sentence."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "integer"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "score", "hook", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rankings"],
    "additionalProperties": False,
}


def enabled() -> bool:
    return secret_present("ANTHROPIC_API_KEY")


def build_prompt(campaign: Campaign | None, candidates: list[Any]) -> str:
    brief = ""
    if campaign is not None:
        brief = (f"Campaign: {campaign.name}\nBrand: {campaign.brand}\nCategory: {campaign.category}\n"
                 f"Brief:\n{(campaign.description or '')[:3000]}\n\n")
    parts = [brief + "Candidates:"]
    for i, m in enumerate(candidates):
        parts.append(f"<excerpt id={i} duration={m.end - m.start:.0f}s>\n{m.text}\n</excerpt>")
    return "\n".join(parts)


def rerank(s: Session, campaign: Campaign | None, candidates: list[Any], n: int, client: Any = None) -> list[Any]:
    """Return the best `n` candidates. Falls back to the heuristic order on any problem."""
    candidates = candidates[:MAX_CANDIDATES]
    if len(candidates) <= 1 or not (enabled() or client is not None):
        return candidates[:n]
    try:
        check_spend(s, EST_CALL_USD, "AI moment ranking")
    except SpendLimitExceeded as e:
        audit(s, "ai_rank", "skipped_spend_limit", level="warning", reason=str(e))
        return candidates[:n]
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(campaign, candidates)}],
        )
    except Exception as e:  # noqa: BLE001 - never block production on the optional ranker
        audit(s, "ai_rank", "call_failed", level="warning", error=f"{type(e).__name__}: {str(e)[:200]}")
        return candidates[:n]
    usage = getattr(response, "usage", None)
    if usage is not None:
        cost = (usage.input_tokens or 0) * USD_PER_INPUT_TOKEN + (usage.output_tokens or 0) * USD_PER_OUTPUT_TOKEN
        record_cost(s, "ai_api", round(cost, 5), f"moment ranking ({usage.input_tokens} in / {usage.output_tokens} out)",
                    campaign_id=campaign.id if campaign is not None else None)
    if response.stop_reason != "end_turn":
        audit(s, "ai_rank", "unusable_response", level="warning", stop_reason=response.stop_reason)
        return candidates[:n]
    try:
        text = next(b.text for b in response.content if b.type == "text")
        rankings = json.loads(text)["rankings"]
    except (StopIteration, ValueError, KeyError) as e:
        audit(s, "ai_rank", "parse_failed", level="warning", error=str(e)[:200])
        return candidates[:n]
    by_id = {r["id"]: r for r in rankings if isinstance(r.get("id"), int) and 0 <= r["id"] < len(candidates)}
    for i, m in enumerate(candidates):
        r = by_id.get(i)
        if r is None:
            continue
        ai = max(0, min(100, int(r["score"])))
        m.breakdown["ai_score"] = ai
        m.breakdown["ai_reason"] = str(r.get("reason", ""))[:200]
        m.score = round(0.5 * m.score + 0.5 * ai, 1) if ai > 0 else 0.0
        hook = str(r.get("hook", "")).strip()
        if 0 < len(hook) <= 60:
            m.hook = hook
    ranked = sorted((m for m in candidates if m.score > 0), key=lambda m: m.score, reverse=True)
    audit(s, "ai_rank", "ranked", candidates=len(candidates), kept=min(n, len(ranked)))
    return ranked[:n]
