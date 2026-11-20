"""Whop (whop.com) — official REST API, https://api.whop.com/api/v1 (OpenAPI: /api/v1/openapi.json).

From the spec (fetched 2026-11-20):
* GET  /bounties            — "for a user token, the bounties the user can see and work"; filters: status,
                               country (ISO-2), query, first/after cursor pagination.
* POST /bounty_submissions  — body {bounty_id, deliverable:{urls,file_ids,caption}}. "Requires a user
                               credential — account API keys cannot author submissions."
* User credential = OAuth 2.1 + PKCE token ("Sign in with Whop"), endpoints under https://api.whop.com/oauth/.
* Bounties are FIXED-reward-per-accepted-submission tasks (gross/net_reward_amount, spots_remaining).
  CPM "Content Rewards" campaigns are run by Content Rewards Inc. and are NOT in this API.
Status: auto_untested until WHOP_USER_TOKEN is provided and a live call succeeds.
"""
from __future__ import annotations

from ..config import secret
from .base import CampaignData, MarketplaceAdapter, SubmitResult

API = "https://api.whop.com/api/v1"
API_VERSION_DATE = "2026-11-01"


class WhopAdapter(MarketplaceAdapter):
    name = "whop"
    display_name = "Whop (bounties)"
    home_url = "https://whop.com"
    capabilities = {"discover": "auto_untested", "campaign_brief": "auto_untested", "join": "unsupported",
                    "submit": "auto_untested", "review_status": "auto_untested", "earnings": "manual"}

    def _headers(self) -> dict[str, str]:
        tok = secret("WHOP_USER_TOKEN")
        if not tok:
            raise RuntimeError("WHOP_USER_TOKEN not configured (user OAuth token required)")
        return {"Authorization": f"Bearer {tok}", "Api-Version-Date": API_VERSION_DATE}

    def discover(self) -> list[CampaignData]:
        out, after = [], None
        for _ in range(10):  # max 10 pages per cycle
            params = {"status": "open", "first": 50}
            if after:
                params["after"] = after
            data = self._get(f"{API}/bounties", params=params, headers=self._headers()).json()
            for b in data.get("data", []):
                out.append(CampaignData(
                    marketplace="whop", external_id=b["id"], name=b.get("title", ""),
                    url=f"https://whop.com/bounties/{b['id']}", brand=(b.get("poster") or {}).get("name", ""),
                    description=b.get("description", ""), payment_model="fixed",
                    fixed_reward_usd=b.get("net_reward_amount"),
                    budget_total_usd=b.get("budget_amount"),
                    budget_available_usd=(b.get("net_reward_amount") or 0) * (b.get("spots_remaining") or 0),
                    geography=b.get("allowed_country_codes") or [], status=b.get("status", "open"),
                    max_payout_per_account_usd=(b.get("net_reward_amount") or 0) * (b.get("accepted_submissions_per_user_limit") or 1),
                    brief_complete=True, raw={k: b.get(k) for k in ("currency", "spots_remaining",
                                                                  "accepted_deliverable_types", "business_goal_type")}))
            pi = data.get("page_info") or {}
            if not pi.get("has_next_page"):
                break
            after = pi.get("end_cursor")
        return out

    def submit(self, campaign_external_id: str, post_url: str, account_handle: str) -> SubmitResult:
        try:
            r = self.client.post(f"{API}/bounty_submissions", headers=self._headers(),
                                 json={"bounty_id": campaign_external_id, "deliverable": {"urls": [post_url]}})
        except Exception as e:  # noqa: BLE001
            return SubmitResult(ok=False, error=type(e).__name__)
        if r.status_code in (200, 201):
            return SubmitResult(ok=True, confirmation=str(r.json().get("id", "")))
        return SubmitResult(ok=False, error=f"HTTP {r.status_code}: {r.text[:300]}")
