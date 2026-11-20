"""Social Account Manager: account registry helpers and the campaign<->account assignment algorithm."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Assignment, Campaign, SocialAccount, Submission, now
from .ops import audit

MAX_CAMPAIGNS_PER_ACCOUNT = 3      # keeps each account's niche coherent
MAX_ACCOUNTS_PER_CAMPAIGN = 3      # never throw every account at one campaign


def account_fit(a: SocialAccount, c: Campaign) -> tuple[float, str]:
    """0 means ineligible. Higher is better."""
    if a.platform not in (c.platforms or []):
        return 0, "platform not accepted"
    if a.status not in ("registered", "active"):
        return 0, f"account {a.status}"
    if c.language and a.language and a.language != c.language:
        return 0, f"language {a.language} != {c.language}"
    if c.marketplace in ("clipster",) and c.marketplace not in (a.marketplaces or []) and a.marketplaces:
        return 0, "not linked on marketplace"
    score = 1.0
    reasons = []
    if a.niche and a.niche.lower() in f"{c.category} {c.name}".lower():
        score += 2
        reasons.append("niche match")
    if a.avg_views:
        score += min(2.0, a.avg_views / 5000)
        reasons.append(f"avg views {a.avg_views:.0f}")
    if a.health == "good":
        score += 1
    if a.auth_status == "connected":
        score += 0.5
        reasons.append("API publishing")
    return score, ", ".join(reasons) or "eligible"


def assign_accounts(s: Session, c: Campaign) -> list[Assignment]:
    existing = list(s.scalars(select(Assignment).where(Assignment.campaign_id == c.id, Assignment.active)))
    if len(existing) >= MAX_ACCOUNTS_PER_CAMPAIGN:
        return existing
    load = dict(s.execute(select(Assignment.account_id, func.count()).where(Assignment.active)
                          .group_by(Assignment.account_id)).all())
    taken = {a.account_id for a in existing}
    candidates = []
    for a in s.scalars(select(SocialAccount)):
        if a.id in taken or load.get(a.id, 0) >= MAX_CAMPAIGNS_PER_ACCOUNT:
            continue
        fit, why = account_fit(a, c)
        if fit > 0:
            candidates.append((fit - 0.3 * load.get(a.id, 0), a, why))
    candidates.sort(key=lambda t: t[0], reverse=True)
    # one account per platform first (spread), then fill
    chosen, platforms = [], set()
    for fit, a, why in candidates:
        if a.platform not in platforms:
            chosen.append((a, why)); platforms.add(a.platform)
    for fit, a, why in candidates:
        if all(a is not x for x, _ in chosen):
            chosen.append((a, why))
    out = existing[:]
    for a, why in chosen[: MAX_ACCOUNTS_PER_CAMPAIGN - len(existing)]:
        asg = Assignment(campaign_id=c.id, account_id=a.id, reason=why)
        s.add(asg)
        out.append(asg)
        audit(s, "manager", "assign_account", "campaign", c.id, account=a.label, reason=why)
    s.flush()
    return out


def posts_today(s: Session, account_id: int) -> int:
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return s.scalar(select(func.count()).select_from(Submission).where(
        Submission.account_id == account_id, Submission.published_at >= start)) or 0


def account_has_capacity(s: Session, a: SocialAccount) -> bool:
    return posts_today(s, a.id) < a.daily_post_cap


def next_slot(s: Session, a: SocialAccount, min_gap_h: float = 3.0) -> dt.datetime:
    last = s.scalar(select(func.max(Submission.scheduled_for)).where(Submission.account_id == a.id))
    t = now()
    return max(t, last + dt.timedelta(hours=min_gap_h)) if last else t
