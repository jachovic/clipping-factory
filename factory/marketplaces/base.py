"""Marketplace adapter contract. Every adapter declares honestly which operations it can automate:
'auto'        — implemented AND verified against the live service
'auto_untested' — implemented from official docs, not yet verified (needs credentials)
'manual'      — not automatable within the platform's rules; the factory creates a human task
'unsupported' — not available on this platform
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import settings


@dataclass
class CampaignData:
    marketplace: str
    external_id: str
    name: str
    url: str
    brand: str = ""
    category: str = ""
    description: str = ""
    payment_model: str = "cpm"
    cpm_usd: float | None = None
    cpm_max_usd: float | None = None
    fixed_reward_usd: float | None = None
    max_payout_per_video_usd: float | None = None
    max_payout_per_account_usd: float | None = None
    min_payout_usd: float | None = None
    budget_total_usd: float | None = None
    budget_available_usd: float | None = None
    platforms: list[str] = field(default_factory=list)
    language: str = ""
    geography: list[str] = field(default_factory=list)
    requires_application: bool = False
    status: str = "active"
    submission_window_hours: float | None = None
    creator_count: int | None = None
    submission_count: int | None = None
    brief_complete: bool = False
    source_links: list[str] = field(default_factory=list)  # links named in the brief (footage, channels, docs)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmitResult:
    ok: bool
    confirmation: str = ""
    manual_task: str = ""   # instructions when a human has to do it
    error: str = ""


class MarketplaceAdapter:
    name: str = "base"
    display_name: str = "Base"
    home_url: str = ""
    capabilities: dict[str, str] = {}
    listing_exhaustive: bool = True   # discover() returns every live campaign (so missing = closed)
    # Facts from the marketplace's own docs that the engine must respect.
    known_rules: dict[str, Any] = {}

    def __init__(self, client: httpx.Client | None = None, min_interval_s: float = 2.0):
        self.client = client or httpx.Client(timeout=30, follow_redirects=True,
                                             headers={"User-Agent": settings.user_agent})
        self._min_interval = min_interval_s
        self._last = 0.0

    def _get(self, url: str, **kw: Any) -> httpx.Response:
        """Polite GET: at most one request per min_interval_s, raises on HTTP errors."""
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.client.get(url, **kw)
        r.raise_for_status()
        return r

    def discover(self) -> list[CampaignData]:
        raise NotImplementedError(f"{self.name}: discovery is {self.capabilities.get('discover', 'manual')}")

    def enrich(self, c: CampaignData) -> CampaignData:
        """Fetch the full brief for one campaign (optional)."""
        return c

    def submit(self, campaign_external_id: str, post_url: str, account_handle: str) -> SubmitResult:
        return SubmitResult(ok=False, manual_task=(
            f"Submit {post_url} (account {account_handle}) to {self.display_name} campaign "
            f"{campaign_external_id} at {self.home_url}"))


def parse_money(label: str | None) -> tuple[float | None, float | None]:
    """'$$1.50' -> (1.5, 1.5); '⁦$0.50–$1⁩' -> (0.5, 1.0); '$2,000' -> (2000, 2000)."""
    import re
    if not label:
        return None, None
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", label)]
    if not nums:
        return None, None
    return min(nums), max(nums)


def extract_links(text: str | None) -> list[str]:
    """URLs mentioned in a campaign brief, in order, de-duplicated."""
    import re
    seen: list[str] = []
    for u in re.findall(r"https?://[^\s<>()\[\]\"']+", text or ""):
        u = u.rstrip(".,;:!?")
        if u not in seen:
            seen.append(u)
    return seen
