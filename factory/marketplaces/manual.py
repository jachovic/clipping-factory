"""Marketplaces with no public API and login-only (or JavaScript-only) campaign pages.
They participate through manual tasks until an official integration or the operator's own
logged-in browser session is available."""
from __future__ import annotations

from .base import MarketplaceAdapter

_MANUAL = {"discover": "manual", "campaign_brief": "manual", "join": "manual", "submit": "manual",
           "review_status": "manual", "earnings": "manual"}


class ClipsterAdapter(MarketplaceAdapter):
    """Verified from clipster.com/llms-full.txt (2026-11-20): app at app.clipster.gg; creators link
    TikTok/Instagram/YouTube via the platform's own sign-in; submit the post link WITHIN 24 HOURS of
    posting; only views gained after submission count; views must be organic (no bots/ads/engagement
    groups); paid posts must stay public 30 days after payout; payout ~1 week after campaign end;
    $50 minimum withdrawal (PayPal, Visa prepaid, Amazon GC, USDT, bank); no country block; VPN allowed."""
    name = "clipster"
    display_name = "Clipster"
    home_url = "https://app.clipster.gg/discover"
    capabilities = dict(_MANUAL)
    known_rules = {"submission_window_hours": 24, "views_counted": "after submission only",
                   "keep_post_days_after_payout": 30, "min_withdrawal_usd": 50}


class PromoteFunAdapter(MarketplaceAdapter):
    """promote.fun serves a JavaScript-only app; no API, docs or llms.txt content found (2026-11-20)."""
    name = "promote_fun"
    display_name = "Promote.fun"
    home_url = "https://www.promote.fun"
    capabilities = dict(_MANUAL)


class PrimeOraclesAdapter(MarketplaceAdapter):
    """primeoracles.com: 'digital products marketplace and content rewards'; JavaScript-only app,
    no public API found (2026-11-20)."""
    name = "prime_oracles"
    display_name = "Prime Oracles"
    home_url = "https://www.primeoracles.com"
    capabilities = dict(_MANUAL)
