"""Campaign Intelligence Engine: ingest, change detection, scoring and profitability."""
from __future__ import annotations

import math
import re
from dataclasses import asdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .marketplaces import ADAPTERS, CampaignData, get_adapter
from .models import Campaign, CampaignEvent, SocialAccount, Submission, now
from .notify import notify
from .ops import audit

TRACKED_FIELDS = ("cpm_usd", "cpm_max_usd", "fixed_reward_usd", "max_payout_per_video_usd", "min_payout_usd",
                  "status", "requires_application", "platforms", "budget_total_usd")

# ------------------------------------------------------------------ language
_STOP = {
    "en": {"the", "and", "you", "with", "for", "this", "must", "your", "from", "clip", "clips"},
    "fr": {"le", "la", "les", "et", "vous", "avec", "pour", "des", "une", "est", "campagne"},
    "es": {"el", "los", "las", "y", "con", "para", "una", "que", "del", "campaña"},
    "de": {"der", "die", "das", "und", "mit", "für", "nicht", "ist", "ein", "eine"},
    "pt": {"o", "os", "as", "e", "com", "para", "uma", "não", "do", "da", "campanha"},
    "pl": {"i", "w", "z", "na", "nie", "się", "jest", "dla", "oraz", "kampania"},
}


_LANG_NAMES = {"arabic": "ar", "spanish": "es", "español": "es", "french": "fr", "français": "fr", "german": "de",
               "deutsch": "de", "portuguese": "pt", "brazil": "pt", "polish": "pl", "polski": "pl", "hindi": "hi",
               "italian": "it", "turkish": "tr", "russian": "ru", "japanese": "ja", "korean": "ko"}
_SCRIPTS = [(r"[\u0600-\u06ff]", "ar"), (r"[\u0400-\u04ff]", "ru"), (r"[\u3040-\u30ff]", "ja"),
            (r"[\uac00-\ud7af]", "ko"), (r"[\u0900-\u097f]", "hi"), (r"[\u4e00-\u9fff]", "zh")]


def detect_language(name: str, description: str) -> str:
    low = name.lower()
    for word, code in _LANG_NAMES.items():
        if re.search(rf"\b{word}\b", low):
            return code
    for pat, code in _SCRIPTS:
        if len(re.findall(pat, f"{name} {description}")) >= 10:
            return code
    m = re.match(r"^\s*\[?(FR|ES|DE|PT|PL|IT|BR)\]?\s", name)
    if m:
        return {"BR": "pt"}.get(m.group(1), m.group(1).lower())
    words = re.findall(r"[a-zà-ÿąćęłńóśźż]+", f"{name} {description}".lower())
    if len(words) < 5:
        return ""
    scores = {lang: sum(w in stop for w in words) for lang, stop in _STOP.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else ""


# ------------------------------------------------------------------- ingest
def upsert_campaign(s: Session, d: CampaignData) -> tuple[Campaign, list[str]]:
    c = s.scalar(select(Campaign).where(Campaign.marketplace == d.marketplace,
                                        Campaign.external_id == d.external_id))
    data = asdict(d)
    data["language"] = data.get("language") or detect_language(d.name, d.description)
    events: list[str] = []
    if c is None:
        c = Campaign(**data)
        s.add(c)
        s.flush()
        s.add(CampaignEvent(campaign_id=c.id, kind="new", detail={"name": d.name}))
        events.append("new")
    else:
        changes = {}
        for f in TRACKED_FIELDS:
            old, new = getattr(c, f), data[f]
            if new is not None and old != new:
                changes[f] = {"old": old, "new": new}
        for k, v in data.items():
            if v is not None and v != "" and v != []:
                setattr(c, k, v)
        c.last_seen_at = now()
        if changes:
            kind = "closed" if changes.get("status", {}).get("new") in ("closed", "locked") else "changed"
            s.add(CampaignEvent(campaign_id=c.id, kind=kind, detail=changes))
            events.append(kind)
        if c.budget_total_usd and c.budget_available_usd is not None and \
                c.budget_available_usd < 0.1 * c.budget_total_usd and c.status == "active":
            prev = s.scalar(select(CampaignEvent).where(CampaignEvent.campaign_id == c.id,
                                                        CampaignEvent.kind == "low_budget"))
            if prev is None:
                s.add(CampaignEvent(campaign_id=c.id, kind="low_budget",
                                    detail={"available": c.budget_available_usd}))
                events.append("low_budget")
    return c, events


def discover_all(s: Session, marketplaces: list[str] | None = None, enrich_top: int = 15) -> dict[str, Any]:
    """Run discovery on every auto-capable adapter, enrich the most promising briefs, rescore."""
    summary: dict[str, Any] = {}
    for name, cls in ADAPTERS.items():
        if marketplaces and name not in marketplaces:
            continue
        if cls.capabilities.get("discover") not in ("auto", "auto_untested"):
            summary[name] = "manual"
            continue
        adapter = get_adapter(name)
        try:
            found = adapter.discover()
        except Exception as e:  # noqa: BLE001
            summary[name] = f"error: {type(e).__name__}: {str(e)[:120]}"
            audit(s, "discovery", "discover_failed", "marketplace", name, level="warning", error=str(e)[:300])
            continue
        # enrich the best-looking active ones first (cheap pre-sort on headline rate & budget)
        found.sort(key=lambda d: (d.status == "active", (d.cpm_usd or 0) * math.log10((d.budget_available_usd or 10) + 10)),
                   reverse=True)
        n_new = n_changed = 0
        seen_ids = set()
        for i, d in enumerate(found):
            if i < enrich_top and d.status == "active":
                try:
                    d = adapter.enrich(d)
                except Exception as e:  # noqa: BLE001
                    audit(s, "discovery", "enrich_failed", "campaign", d.external_id, level="warning", error=str(e)[:200])
            c, ev = upsert_campaign(s, d)
            seen_ids.add(c.id)
            n_new += "new" in ev
            n_changed += bool(set(ev) - {"new"})
        if hasattr(adapter, "sitemap"):
            extra = crawl_sitemap(s, adapter, seen_external={d.external_id for d in found})
            n_new += extra["new"]
            n_changed += extra["changed"]
        # campaigns that vanished from an exhaustive listing are treated as closed
        for c in s.scalars(select(Campaign).where(Campaign.marketplace == name, Campaign.status == "active")):
            if adapter.listing_exhaustive and c.id not in seen_ids and found:
                c.status = "closed"
                s.add(CampaignEvent(campaign_id=c.id, kind="closed", detail={"reason": "no longer listed"}))
        summary[name] = {"found": len(found), "new": n_new, "changed": n_changed}
        audit(s, "discovery", "discover_ok", "marketplace", name, **summary[name])
    s.flush()
    rescore_all(s)
    notify_events(s)
    return summary


SITEMAP_FETCH_PER_CYCLE = 60


def crawl_sitemap(s: Session, adapter, seen_external: set[str]) -> dict[str, int]:
    """Fetch public detail pages for campaigns that are new or changed since our last read (sitemap lastmod)."""
    out = {"new": 0, "changed": 0, "fetched": 0}
    try:
        entries = adapter.sitemap()
    except Exception as e:  # noqa: BLE001
        audit(s, "discovery", "sitemap_failed", "marketplace", adapter.name, level="warning", error=str(e)[:200])
        return out
    known = {c.external_id: c for c in s.scalars(select(Campaign).where(Campaign.marketplace == adapter.name))}
    todo = []
    for ext_id, lastmod in sorted(entries, key=lambda e: e[1], reverse=True):   # newest first
        if ext_id in seen_external:
            continue
        c = known.get(ext_id)
        if c is None or (c.raw or {}).get("sitemap_lastmod") != lastmod:
            todo.append((ext_id, lastmod))
    for ext_id, lastmod in todo[:SITEMAP_FETCH_PER_CYCLE]:
        try:
            d = adapter.fetch_campaign(ext_id)
        except Exception as e:  # noqa: BLE001
            audit(s, "discovery", "detail_failed", "campaign", ext_id, level="warning", error=str(e)[:200])
            continue
        out["fetched"] += 1
        if d is None:
            continue
        d.raw["sitemap_lastmod"] = lastmod
        _, ev = upsert_campaign(s, d)
        out["new"] += "new" in ev
        out["changed"] += bool(set(ev) - {"new"})
    out["backlog"] = max(0, len(todo) - SITEMAP_FETCH_PER_CYCLE)
    audit(s, "discovery", "sitemap_crawl", "marketplace", adapter.name, **out)
    return out


def notify_events(s: Session) -> None:
    for ev in s.scalars(select(CampaignEvent).where(CampaignEvent.notified.is_(False))):
        c = s.get(Campaign, ev.campaign_id)
        ev.notified = True
        if ev.kind == "new" and c.priority == "HIGH":
            notify(s, "new_campaign", f"HIGH priority campaign {c.id} (score {c.score:.0f}): {c.name} "
                                      f"[{c.marketplace}] {fmt_rate(c)} — {c.url}")
        elif ev.kind in ("changed", "closed", "low_budget") and (c.selected or c.joined):
            notify(s, "campaign_change", f"Campaign {c.id} {ev.kind}: {c.name} {ev.detail}")


def fmt_rate(c: Campaign) -> str:
    if c.payment_model == "cpm" and c.cpm_usd is not None:
        r = f"${c.cpm_usd:.2f}" + (f"–${c.cpm_max_usd:.2f}" if c.cpm_max_usd and c.cpm_max_usd != c.cpm_usd else "")
        return r + " CPM"
    if c.payment_model == "fixed" and c.fixed_reward_usd:
        return f"${c.fixed_reward_usd:.2f}/accepted"
    return c.payment_model


# ------------------------------------------------------------ profitability
# Prior assumptions until we have our own history. Every number is labelled as an assumption.
SCENARIOS = {
    #              views/clip  approval prob
    "worst":        (0, 0.0),
    "conservative": (800, 0.5),
    "expected":     (4_000, 0.7),
    "high":         (40_000, 0.85),
}
DEFAULT_PRODUCTION_MIN_PER_CLIP = 12   # operator/agent minutes incl. QC and posting
DEFAULT_VARIABLE_COST_PER_CLIP = 0.05  # local engine: compute/storage; Crayo cost added when used


def views_prior(s: Session, c: Campaign) -> dict[str, tuple[int, float]]:
    """Replace priors with our own median views/approval rate once we have >=10 data points."""
    rows = s.execute(select(Submission.views, Submission.status).where(
        Submission.marketplace == c.marketplace, Submission.views.is_not(None))).all()
    if len(rows) < 10:
        return dict(SCENARIOS)
    views = sorted(r[0] for r in rows)
    decided = [r for r in rows if r[1] in ("approved", "rejected", "earning", "pending_payout", "paid")]
    appr = (sum(r[1] != "rejected" for r in decided) / len(decided)) if decided else 0.6
    q = lambda p: views[min(len(views) - 1, int(p * len(views)))]  # noqa: E731
    return {"worst": (0, 0.0), "conservative": (q(0.25), appr * 0.8), "expected": (q(0.5), appr),
            "high": (q(0.9), min(1.0, appr * 1.1))}


def clip_gross(c: Campaign, views: int) -> float:
    if c.payment_model == "fixed":
        return c.fixed_reward_usd or 0.0
    if c.payment_model != "cpm" or not c.cpm_usd:
        return 0.0
    g = views * c.cpm_usd / 1000
    if c.min_payout_usd and g < c.min_payout_usd:
        return 0.0                      # below the review threshold -> earns nothing
    if c.max_payout_per_video_usd:
        g = min(g, c.max_payout_per_video_usd)
    return g


def profitability(s: Session, c: Campaign, clips: int = 6, cost_per_clip: float | None = None,
                  minutes_per_clip: float = DEFAULT_PRODUCTION_MIN_PER_CLIP) -> dict[str, Any]:
    cost_per_clip = DEFAULT_VARIABLE_COST_PER_CLIP if cost_per_clip is None else cost_per_clip
    out: dict[str, Any] = {"clips": clips, "cost_per_clip_usd": cost_per_clip, "minutes_per_clip": minutes_per_clip,
                           "assumptions": "views/approval are priors until >=10 own data points"}
    hours = clips * minutes_per_clip / 60
    for name, (views, p_appr) in views_prior(s, c).items():
        gross = clip_gross(c, views) * p_appr * clips
        if c.budget_available_usd is not None:
            gross = min(gross, max(0.0, c.budget_available_usd))
        if c.max_payout_per_account_usd:
            gross = min(gross, c.max_payout_per_account_usd)
        net = gross - cost_per_clip * clips
        out[name] = {"views_per_clip": views, "approval_prob": round(p_appr, 2), "gross_usd": round(gross, 2),
                     "net_usd": round(net, 2), "net_per_hour_usd": round(net / hours, 2) if hours else 0.0}
    if c.payment_model == "cpm" and c.cpm_usd and c.min_payout_usd:
        out["min_views_to_earn"] = int(math.ceil(c.min_payout_usd * 1000 / c.cpm_usd))
    if c.payment_model == "cpm" and c.cpm_usd and c.max_payout_per_video_usd:
        out["views_at_cap"] = int(c.max_payout_per_video_usd * 1000 / c.cpm_usd)
    out["payout_wait_days"] = {"content_rewards": 10, "clipster": "~7 after campaign end"}.get(c.marketplace, "unknown")
    return out


# ------------------------------------------------------------------ scoring
# Content types this factory can produce. UGC (filming yourself) and slideshows are out of scope.
PRODUCIBLE_TYPES = {"clipping"}


def producible(c: Campaign) -> bool:
    types = (c.raw or {}).get("content_types")
    return True if not types else bool(PRODUCIBLE_TYPES & set(types))


def score_campaign(s: Session, c: Campaign, accounts: list[SocialAccount]) -> tuple[float, dict[str, float]]:
    if c.status != "active":
        return 0.0, {"inactive": 0.0}
    if not producible(c):
        return 0.0, {"not_producible": 0.0}
    b: dict[str, float] = {}
    # 1. headline rate (15)
    if c.payment_model == "cpm":
        b["rate"] = 15 * min(1.0, (c.cpm_usd or 0) / 2.0)
    elif c.payment_model == "fixed":
        b["rate"] = 15 * min(1.0, (c.fixed_reward_usd or 0) / 50)
    else:  # pool / unknown: payout depends on rank, hard to forecast
        b["rate"] = 5.0
    # 2. expected value per clip from the profitability model (20): $5+/clip expected net = full
    prof = c.profitability or {}
    exp_net = (prof.get("expected") or {}).get("net_usd", 0) / max(1, prof.get("clips", 1))
    b["expected_value"] = 20 * min(1.0, max(0.0, exp_net) / 5)
    # 3. remaining budget (10), log scale; $10k+ = full
    avail = c.budget_available_usd
    b["budget"] = 10 * min(1.0, math.log10(max(avail, 1)) / 4) if avail is not None else 3.0
    # 4. account/platform/language compatibility (15); accounts without a registered username count half
    plats = set(c.platforms or [])
    eligible = sum(1.0 if a.username else 0.5 for a in accounts
                   if a.platform in plats and a.status in ("registered", "active")
                   and (not c.language or a.language == c.language))
    b["compatibility"] = 15 * min(1.0, eligible / 4) if plats else 4.0
    # 5. minimum-view threshold (10): views needed before a clip earns anything
    if c.payment_model == "cpm" and c.cpm_usd:
        need = (c.min_payout_usd or 0) * 1000 / c.cpm_usd
        b["threshold"] = 10 * (1 - min(1.0, need / 10_000))
    else:
        b["threshold"] = 4.0
    # 5. per-video cap headroom (5)
    if c.payment_model == "cpm" and c.cpm_usd and c.max_payout_per_video_usd:
        b["cap"] = 5 * min(1.0, (c.max_payout_per_video_usd * 1000 / c.cpm_usd) / 200_000)
    else:
        b["cap"] = 3.0
    # 6. production difficulty (10): plain clipping is easiest for us
    text = f"{c.name} {c.description} {c.category}".lower()
    if "ugc" in text or "testimonial" in text:
        b["difficulty"] = 2.0
    elif "logo" in text or "edit" in text:
        b["difficulty"] = 6.0
    else:
        b["difficulty"] = 10.0
    # 7. approval probability (10)
    appr = 10.0
    if c.requires_application:
        appr -= 4
    if not c.brief_complete:
        appr -= 2  # some rules only visible after login
    hist = s.execute(select(Submission.status, func.count()).where(Submission.campaign_id == c.id)
                     .group_by(Submission.status)).all()
    rej = sum(n for st, n in hist if st == "rejected")
    tot = sum(n for st, n in hist if st in ("approved", "rejected", "earning", "pending_payout", "paid"))
    if tot >= 3:
        appr = 10 * (1 - rej / tot)
    b["approval"] = max(0.0, appr)
    # 8. competition (5): budget per active creator
    if c.creator_count and avail:
        b["competition"] = 5 * min(1.0, (avail / max(c.creator_count, 1)) / 500)
    else:
        b["competition"] = 3.0
    total = round(sum(b.values()), 1)
    return total, {k: round(v, 1) for k, v in b.items()}


def priority_for(score: float) -> str:
    return "HIGH" if score >= 75 else "MEDIUM" if score >= 55 else "LOW"


def rescore_all(s: Session) -> None:
    accounts = list(s.scalars(select(SocialAccount)))
    for c in s.scalars(select(Campaign)):
        c.profitability = profitability(s, c)      # first: the score uses it
        c.score, c.score_breakdown = score_campaign(s, c, accounts)
        c.priority = priority_for(c.score) if c.status == "active" and producible(c) else "LOW"
        budget_known = c.budget_available_usd is not None or \
            ADAPTERS.get(c.marketplace, None) is not None and ADAPTERS[c.marketplace].known_rules.get("active_implies_budget")
        if c.priority == "HIGH" and (not c.platforms or not budget_known):
            c.priority = "MEDIUM"   # not enough verified information to commit production


def recommend_allocation(s: Session, top: int = 5) -> list[dict[str, Any]]:
    """Where to spend production capacity: rank by expected net per production hour, then score."""
    rows = []
    for c in s.scalars(select(Campaign).where(Campaign.status == "active")):
        p = c.profitability or {}
        rows.append({"id": c.id, "name": c.name, "marketplace": c.marketplace, "score": c.score,
                     "priority": c.priority, "rate": fmt_rate(c),
                     "expected_net_per_hour": (p.get("expected") or {}).get("net_per_hour_usd", 0),
                     "conservative_net": (p.get("conservative") or {}).get("net_usd", 0)})
    rows.sort(key=lambda r: (r["priority"] == "HIGH", r["expected_net_per_hour"], r["score"] or 0), reverse=True)
    return rows[:top]
