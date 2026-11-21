"""Vyro (vyro.com) — a Beast Industries company.

Verified 2026-11-20 from vyro.com/llms.txt: "Vyro does not currently publish a public API, OpenAPI spec,
webhooks, or an MCP server." Every page serves markdown (append .md), which this adapter reads.
Rates are quoted per 1M views (e.g. $2,000 per 1M = $2.00 CPM). Joining/submitting is in app.vyro.com
(login) -> manual tasks.
"""
from __future__ import annotations

import re

from .base import CampaignData, MarketplaceAdapter, parse_money

BASE = "https://vyro.com"
_CARD = re.compile(r"\[###\s*(?P<brand>[^\n]+?)\n(?P<body>.*?)\]\((?P<url>https://vyro\.com/campaigns/[^)\s]+)\)", re.S)


def parse_board(md: str) -> list[CampaignData]:
    out = []
    for m in _CARD.finditer(md):
        body = [ln.strip() for ln in m.group("body").splitlines() if ln.strip()]
        url = m.group("url")
        slug = url.rsplit("/", 1)[-1]
        locked = any("locked for new posts" in ln.lower() for ln in body)
        rate_line = next((ln for ln in body if ln.startswith("$")), "")
        unit = next((ln for ln in body if re.match(r"per\s+1\s*[mk]\b", ln, re.I)), "")
        lo, hi = parse_money(rate_line)
        per = 1_000_000 if "1m" in unit.lower() else 1000 if "1k" in unit.lower() else None
        cpm_lo = lo * 1000 / per if (lo is not None and per) else None
        cpm_hi = hi * 1000 / per if (hi is not None and per) else None
        title_candidates = [ln for ln in body if not re.match(r"^(\d+[hdwm] ago|per view|locked|\$|per )", ln, re.I)
                            and "•" not in ln]
        name = title_candidates[0] if title_candidates else slug
        model = "cpm" if per else "unknown"
        out.append(CampaignData(marketplace="vyro", external_id=slug, name=name, url=url,
                                brand=m.group("brand").strip(), payment_model=model,
                                cpm_usd=cpm_lo, cpm_max_usd=cpm_hi,
                                status="locked" if locked else "active",
                                raw={"card": body, "rate_unit": unit}))
    return out


class VyroAdapter(MarketplaceAdapter):
    name = "vyro"
    display_name = "Vyro"
    home_url = "https://app.vyro.com"
    capabilities = {"discover": "auto", "campaign_brief": "auto", "join": "manual", "submit": "manual",
                    "review_status": "manual", "earnings": "manual"}
    known_rules = {"api": "none (official statement)", "rate_unit": "per 1M views",
                   "active_implies_budget": True,  # campaigns are 'Locked for new posts' once they stop
                   "review": "brief check runs after post is public (third-party report, unverified)"}

    def discover(self) -> list[CampaignData]:
        return parse_board(self._get(f"{BASE}/campaigns.md").text)

    def enrich(self, c: CampaignData) -> CampaignData:
        html = self._get(c.url).text.replace('\\"', '"')
        m = re.search(r'"PlatformIcons".*?"platforms":\[([^\]]*)\]', html, re.S) or \
            re.search(r'"platforms":\[([^\]]*)\]', html)
        if m:
            c.platforms = sorted({p.strip('" ').replace("twitter", "x") for p in m.group(1).split(",") if p.strip('" ')})
        md = self._get(c.url + ".md").text
        c.raw["detail_md"] = md[:6000]
        c.raw["rates_may_change"] = "rates may change" in md.lower()
        return c
