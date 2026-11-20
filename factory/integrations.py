"""Integration Setup Manager: registry of every connection, live connection tests,
and INTEGRATION_STATUS.md generation. Tests never print or store secret values."""
from __future__ import annotations

from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ROOT, secret, secret_present, settings
from .marketplaces import ADAPTERS
from .models import Integration, SocialAccount, now
from .ops import audit

# Facts per social platform's OFFICIAL publishing API (see INTEGRATIONS.md for sources & caveats).
SOCIAL_PUBLISHING = {
    "youtube": {"api": "YouTube Data API v3 videos.insert", "auth": "Google OAuth 2.0 (youtube.upload)",
                "notes": "Unverified API projects upload as PRIVATE until Google audits the project."},
    "tiktok": {"api": "TikTok Content Posting API (Direct Post)", "auth": "TikTok Login Kit OAuth (video.publish)",
               "notes": "Unaudited client apps can only post with SELF_ONLY (private) visibility."},
    "instagram": {"api": "Instagram Graph API content publishing (REELS)", "auth": "Meta OAuth",
                  "notes": "Professional (Business/Creator) accounts only; video must be at a public URL."},
    "x": {"api": "X API v2 POST /2/tweets + media upload", "auth": "OAuth 2.0 PKCE / OAuth 1.0a user context",
          "notes": "Write access depends on the developer plan tier; check current X pricing."},
    "facebook": {"api": "Graph API Page video_reels", "auth": "Meta OAuth (Page token)",
                 "notes": "Pages only."},
}

SEED: list[dict[str, Any]] = [
    {"service": "crayo", "label": "Crayo", "kind": "tool", "auth_method": "API key (CRAYO_API_KEY, crayo_sk_…)",
     "operations": {"account": "auto_untested", "upload": "auto_untested", "autoclip": "auto_untested",
                    "transcribe": "auto_untested", "export": "auto_untested", "webhooks": "auto_untested",
                    "view_tracker": "auto_untested"},
     "manual_action": "Create an API key on the Developers page of the Crayo app and put it in .env as CRAYO_API_KEY."},
    {"service": "telegram", "label": "Telegram bot", "kind": "notify", "auth_method": "Bot token + owner chat ID",
     "operations": {"notify": "auto_untested", "commands": "auto_untested"},
     "manual_action": "Create a bot with @BotFather; set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID in .env."},
    {"service": "local_engine", "label": "Local FFmpeg + Whisper", "kind": "tool", "auth_method": "none",
     "operations": {"transcribe": "auto", "detect_moments": "auto", "render_9x16": "auto", "captions": "auto"}},
]
for name, cls in ADAPTERS.items():
    SEED.append({"service": name, "label": cls.display_name, "kind": "marketplace",
                 "auth_method": {"whop": "OAuth 2.1 + PKCE user token (WHOP_USER_TOKEN)"}.get(name, "web login (manual)"),
                 "operations": dict(cls.capabilities),
                 "manual_action": {"whop": "Create a Whop OAuth app and authorise it once (see INTEGRATIONS.md)."}
                 .get(name, f"Sign in at {cls.home_url} and link your social accounts there.")})

SOCIAL_SEED = [("tiktok", 5), ("instagram", 2), ("x", 2), ("youtube", 2)]


def seed(s: Session) -> None:
    for item in SEED:
        row = s.scalar(select(Integration).where(Integration.service == item["service"],
                                                 Integration.label == item["label"]))
        if row is None:
            s.add(Integration(**item))
        else:  # keep status/test results, refresh declared capabilities
            row.operations = {**item.get("operations", {}), **{k: v for k, v in row.operations.items() if v == "auto"}}
            row.auth_method = item["auth_method"]
            row.manual_action = row.manual_action or item.get("manual_action", "")
    for platform, n in SOCIAL_SEED:
        for i in range(1, n + 1):
            label = f"{platform}-{i}"
            if s.scalar(select(SocialAccount).where(SocialAccount.platform == platform,
                                                    SocialAccount.label == label)) is None:
                s.add(SocialAccount(platform=platform, label=label))
            if s.scalar(select(Integration).where(Integration.service == platform,
                                                  Integration.label == label)) is None:
                info = SOCIAL_PUBLISHING[platform]
                s.add(Integration(service=platform, label=label, kind="social", auth_method=info["auth"],
                                  operations={"publish": "manual", "metrics": "manual"},
                                  manual_action=f"Provide username; then authorise via {info['api']}. {info['notes']}"))
    s.flush()


# ---------------------------------------------------------------- live tests
def _test_crayo() -> tuple[str, str, dict]:
    if not secret_present("CRAYO_API_KEY"):
        return "awaiting_user", "CRAYO_API_KEY not set", {}
    from .crayo import CrayoClient
    acct = CrayoClient().account()
    info = {"plan": acct["account"]["plan"], "credits": acct["credits"]}
    return "connected", "", info


def _test_telegram() -> tuple[str, str, dict]:
    tok = secret("TELEGRAM_BOT_TOKEN")
    if not tok:
        return "awaiting_user", "TELEGRAM_BOT_TOKEN not set", {}
    r = httpx.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=15)
    if r.status_code != 200:
        return "error", f"getMe HTTP {r.status_code}", {}
    if not settings.telegram_owner_id:
        return "partial", "TELEGRAM_OWNER_ID not set — bot will ignore everyone", {"bot": r.json()["result"]["username"]}
    return "connected", "", {"bot": r.json()["result"]["username"]}


def _test_marketplace(name: str) -> Callable[[], tuple[str, str, dict]]:
    def t() -> tuple[str, str, dict]:
        cls = ADAPTERS[name]
        if cls.capabilities.get("discover") == "manual":
            return "awaiting_user", "no public API; login-only", {}
        if name == "whop" and not secret_present("WHOP_USER_TOKEN"):
            return "awaiting_user", "WHOP_USER_TOKEN not set", {}
        found = cls().discover()
        st = "partial" if any(v == "manual" for v in cls.capabilities.values()) else "connected"
        return st, "", {"campaigns_visible": len(found)}
    return t


def _test_local() -> tuple[str, str, dict]:
    import shutil
    if not shutil.which("ffmpeg"):
        return "error", "ffmpeg missing", {}
    import faster_whisper  # noqa: F401
    return "connected", "", {"whisper_model": settings.whisper_model}


TESTS: dict[str, Callable[[], tuple[str, str, dict]]] = {
    "crayo": _test_crayo, "telegram": _test_telegram, "local_engine": _test_local,
    **{n: _test_marketplace(n) for n in ADAPTERS},
}


def test_all(s: Session) -> dict[str, str]:
    results = {}
    for row in s.scalars(select(Integration).where(Integration.kind != "social")):
        fn = TESTS.get(row.service)
        if not fn:
            continue
        try:
            status, err, info = fn()
        except Exception as e:  # noqa: BLE001
            status, err, info = "error", f"{type(e).__name__}: {str(e)[:200]}", {}
        row.status, row.last_error = status, err
        if status in ("connected", "partial"):
            row.last_test_at = now()
        if info:
            row.account_identifier = ", ".join(f"{k}={v}" for k, v in info.items())[:256]
            if row.service == "crayo":
                for op in row.operations:
                    row.operations[op] = "auto" if op == "account" else row.operations[op]
        results[row.label] = status
        audit(s, "integrations", "test", "integration", row.label, status=status, error=err)
    return results


# ----------------------------------------------------------- status report
def write_status_md(s: Session) -> str:
    rows = list(s.scalars(select(Integration).order_by(Integration.kind, Integration.service, Integration.label)))
    lines = ["# INTEGRATION_STATUS", "", f"_Generated by `factory integrations` at {now():%Y-%m-%d %H:%M} UTC. "
             "Do not edit by hand — the database is the source of truth._", "",
             "| Service | Kind | Status | Auth method | Identifier | Operations | Last OK test | Error | Required manual action |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ops = ", ".join(f"{k}:{v}" for k, v in (r.operations or {}).items())
        lines.append(f"| {r.label} | {r.kind} | **{r.status}** | {r.auth_method} | {r.account_identifier or '—'} | "
                     f"{ops} | {r.last_test_at or '—'} | {r.last_error or '—'} | "
                     f"{r.manual_action if r.status != 'connected' else '—'} |")
    lines += ["", "Operation legend: `auto` verified live · `auto_untested` implemented from official docs, awaiting "
              "credentials · `manual` human task (Telegram) · `unsupported` not offered by the platform."]
    text = "\n".join(lines) + "\n"
    (ROOT / "INTEGRATION_STATUS.md").write_text(text)
    return text
