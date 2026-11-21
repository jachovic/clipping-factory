"""Content Rewards (contentrewards.com) — Content Rewards Inc.

Verified 2026-11-20:
* No public API is documented, and robots.txt disallows /api/. We therefore read ONLY the public,
  crawlable pages (/c/discover and /c/campaigns/<id>), which embed the campaign data as a dehydrated
  React-Query payload. Polite rate: 1 request / 2 s.
* Joining, submitting and payout happen in the logged-in app (contentrewards.com/login or inside Whop).
  Those steps are MANUAL tasks until an official integration exists.
* FAQ facts: every submission is reviewed by the brand (nothing auto-approves); CPM payouts in rounds of
  7 days earning + 3-day hold; a clip usually earns for 30 days after submission; each submission
  carries a bot-likelihood score.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .base import CampaignData, MarketplaceAdapter, extract_links, parse_money

BASE = "https://contentrewards.com"
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


def rsc_text(html: str) -> str:
    """Concatenate the Next.js flight-data chunks embedded in a page."""
    return "".join(json.loads('"' + c + '"') for c in _PUSH.findall(html))


def iter_objects(text: str, marker: str) -> Iterator[dict[str, Any]]:
    dec = json.JSONDecoder()
    i = 0
    while (i := text.find(marker, i)) >= 0:
        try:
            obj, i = dec.raw_decode(text, i)
            yield obj
        except json.JSONDecodeError:
            i += len(marker)


def _clean(label: str | None) -> str:
    return (label or "").replace("$$", "$").replace("⁦", "").replace("⁩", "")


def parse_discover(html: str) -> list[CampaignData]:
    out: dict[str, CampaignData] = {}
    for item in iter_objects(rsc_text(html), '{"campaign":{'):
        c = item.get("campaign") or {}
        if not c.get("id") or c["id"] in out:
            continue
        lo, hi = parse_money(_clean(c.get("ratePer1kLabel")))
        model = c.get("type") or "unknown"
        status = "upcoming" if c.get("opensAt") else "active"
        if (c.get("availableBudgetRaw") or 0) <= 0:
            status = "closed"
        out[c["id"]] = CampaignData(
            marketplace="content_rewards", external_id=c["id"], name=_clean(c.get("title")),
            url=f"{BASE}/c/campaigns/{c['id']}", brand=c.get("brand", ""), category=c.get("category", ""),
            description=_clean(c.get("description")), payment_model=model,
            cpm_usd=lo if model == "cpm" else None, cpm_max_usd=hi if model == "cpm" else None,
            max_payout_per_video_usd=(c["maxPayoutCents"] / 100) if c.get("maxPayoutCents") else None,
            budget_total_usd=c.get("budgetTotalRaw"), budget_available_usd=c.get("availableBudgetRaw"),
            platforms=sorted(c.get("platforms") or []), requires_application=bool(c.get("requiresApplication")),
            status=status, creator_count=c.get("creatorCountRaw"), submission_count=c.get("submissionCountRaw"),
            raw={"discover": {k: v for k, v in c.items() if k not in ("description",)},
                 "metrics": {k: v for k, v in (item.get("metrics") or {}).items()
                             if k in ("approvedSubmissionCount", "totalViews", "paidOutCents", "creatorCount")}},
        )
    return list(out.values())


def parse_detail(html: str, c: CampaignData) -> CampaignData:
    text = rsc_text(html)
    for obj in iter_objects(text, '{"banner"'):
        if obj.get("id") != c.external_id:
            continue
        c.description = _clean(obj.get("description")) or c.description
        c.source_links = extract_links(c.description)
        st = obj.get("status")
        if st and st != "active":
            c.status = "closed" if st in ("completed", "ended", "canceled", "cancelled", "paused") else st
        conf = obj.get("configuration") or {}
        # a platform entry with rate 0 is not paid -> ignore it entirely
        models = [m for m in (conf.get("payoutModel") or []) if (m.get("rate") or 0) > 0 or c.payment_model != "cpm"]
        if models:
            rates = [m["rate"] / 100 for m in models if m.get("rate") is not None]
            if rates and c.payment_model == "cpm":
                c.cpm_usd, c.cpm_max_usd = min(rates), max(rates)
            mins = [m["minPayout"] / 100 for m in models if m.get("minPayout") is not None]
            maxs = [m["maxPayout"] / 100 for m in models if m.get("maxPayout")]
            c.min_payout_usd = min(mins) if mins else None
            c.max_payout_per_video_usd = min(maxs) if maxs else c.max_payout_per_video_usd
            c.platforms = sorted({m["platform"] for m in models if m.get("platform")}) or c.platforms
        if conf.get("budget"):
            c.budget_total_usd = conf["budget"] / 100
        req = conf.get("requirement") or {}
        c.requires_application = bool(req.get("preApproval", c.requires_application))
        c.raw["detail"] = {"payoutModel": models, "briefCounts": obj.get("briefCounts"),
                           "launchDate": obj.get("launchDate"), "statusReason": obj.get("statusReason"),
                           "feeSchedule": obj.get("feeSchedule"), "metrics": obj.get("metrics")}
        # Content rules / references beyond the description are only shown to logged-in creators.
        counts = obj.get("briefCounts") or {}
        c.brief_complete = not any(counts.get(k) for k in ("contentRules", "creatorRequirements", "references"))
        break
    return c


_SITEMAP_ENTRY = re.compile(r"<loc>https://contentrewards\.com/c/campaigns/([0-9a-f-]{36})</loc>\s*<lastmod>([^<]+)</lastmod>")


def parse_sitemap(xml: str) -> list[tuple[str, str]]:
    return _SITEMAP_ENTRY.findall(xml)


def campaign_from_detail(html: str, external_id: str) -> CampaignData | None:
    """Build a full record from a public campaign page alone (used for sitemap-only campaigns)."""
    text = rsc_text(html)
    for obj in iter_objects(text, '{"banner"'):
        if obj.get("id") != external_id:
            continue
        conf = obj.get("configuration") or {}
        cats = ((conf.get("content") or {}).get("contentCategories") or [])
        metrics = obj.get("metrics") or {}
        budget = (conf.get("budget") or 0) / 100 or None
        spent = (metrics.get("budgetSpentCents") or 0) / 100
        c = CampaignData(marketplace="content_rewards", external_id=external_id, name=_clean(obj.get("name")),
                         url=f"{BASE}/c/campaigns/{external_id}", brand=(obj.get("organization") or {}).get("name", ""),
                         category=cats[0]["name"] if cats else "", payment_model=obj.get("type") or "unknown",
                         budget_total_usd=budget,
                         budget_available_usd=max(0.0, budget - spent) if budget else None,
                         creator_count=metrics.get("creatorCount"),
                         status="active" if obj.get("status") == "active" else "closed")
        return parse_detail(html, c)
    return None


class ContentRewardsAdapter(MarketplaceAdapter):
    name = "content_rewards"
    listing_exhaustive = False   # /c/discover shows a sample; the sitemap is the full index
    display_name = "Content Rewards"
    home_url = BASE
    capabilities = {
        "discover": "auto",           # verified against the live public page
        "campaign_brief": "auto",     # public part; logged-in rules/references are manual
        "join": "manual",
        "submit": "manual",
        "review_status": "manual",
        "earnings": "manual",
    }
    known_rules = {"review": "every submission reviewed by brand; no auto-approval",
                   "payout_cycle_days": 10, "earning_window_days": 30}

    def discover(self) -> list[CampaignData]:
        return parse_discover(self._get(f"{BASE}/c/discover").text)

    def enrich(self, c: CampaignData) -> CampaignData:
        return parse_detail(self._get(c.url).text, c)

    def sitemap(self) -> list[tuple[str, str]]:
        return parse_sitemap(self._get(f"{BASE}/sitemap.xml").text)

    def fetch_campaign(self, external_id: str) -> CampaignData | None:
        return campaign_from_detail(self._get(f"{BASE}/c/campaigns/{external_id}").text, external_id)
