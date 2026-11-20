"""Telegram Control Center (long polling). Only TELEGRAM_OWNER_ID can control it; every other sender is
ignored and logged. The token is read from the environment and never logged."""
from __future__ import annotations

import time
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import campaigns as camp
from . import pipeline
from .config import secret, settings
from .growth import breakdowns, recommendations
from .manager import daily_report, money, overview
from .models import (Approval, Campaign, Clip, Experiment, Integration, Job, LedgerEntry, SocialAccount,
                     Submission, now)
from .ops import audit, enqueue, set_flag, session

HELP = """Commands
/status /report /dailyplan /manager — system state & plans
/campaigns /discover /opportunities /campaign <id>
/accounts /integrations
/production /queue /submissions
/analytics /earnings /profit /costs /strategy /experiments
/approve <id> /reject <id> — pending approvals
/posted <submission_id> <url> — confirm a manual post
/submitted <submission_id> — confirm a manual marketplace submission
/rejected <submission_id> <category> <reason> — record a marketplace rejection
/approved <submission_id> — record marketplace approval
/paid <amount_usd> <marketplace> [note] — record money actually received
/source <campaign_id> <direct-video-url> — fetch official source material
/pause /resume /stopall"""


def _table(rows: list[str], empty: str = "(none)") -> str:
    return "\n".join(rows) if rows else empty


def c_status(s: Session, a: list[str]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in overview(s).items())


def c_campaigns(s: Session, a: list[str]) -> str:
    rows = s.scalars(select(Campaign).where(Campaign.status == "active").order_by(Campaign.score.desc()).limit(15))
    return _table([f"#{c.id} [{c.priority}] {c.score or 0:.0f} {c.name[:40]} — {camp.fmt_rate(c)} ({c.marketplace})"
                   f"{' ✅' if c.selected else ''}" for c in rows])


def c_discover(s: Session, a: list[str]) -> str:
    enqueue(s, "discover", {}, dedupe_key=f"discover:manual:{now():%Y%m%d%H%M}")
    return "Discovery queued. New HIGH-priority campaigns will be announced."


def c_opps(s: Session, a: list[str]) -> str:
    return _table([f"#{r['id']} {r['name'][:40]} [{r['marketplace']}] {r['rate']} score {r['score']:.0f} "
                   f"exp. net/h ${r['expected_net_per_hour']}" for r in camp.recommend_allocation(s, 8)])


def c_campaign(s: Session, a: list[str]) -> str:
    c = s.get(Campaign, int(a[0])) if a and a[0].isdigit() else None
    if not c:
        return "usage: /campaign <id>"
    p = c.profitability or {}
    sc = "\n".join(f"  {k}: net ${p[k]['net_usd']} (views/clip {p[k]['views_per_clip']}, p(appr) {p[k]['approval_prob']})"
                   for k in ("worst", "conservative", "expected", "high") if k in p)
    return (f"#{c.id} {c.name}\n{c.marketplace} · {c.brand} · {c.status} · {c.priority} {c.score}\n{camp.fmt_rate(c)} · "
            f"min ${c.min_payout_usd} · max/video ${c.max_payout_per_video_usd} · budget left ${c.budget_available_usd}\n"
            f"platforms {c.platforms} · lang {c.language or '?'} · application {c.requires_application}\n"
            f"score {c.score_breakdown}\nScenarios (6 clips):\n{sc}\n{c.url}\n\n{(c.description or '')[:600]}")


def c_accounts(s: Session, a: list[str]) -> str:
    return _table([f"{x.label}: @{x.username or '?'} {x.status} auth={x.auth_status} publish={x.publish_method}"
                   for x in s.scalars(select(SocialAccount).order_by(SocialAccount.platform, SocialAccount.label))])


def c_integrations(s: Session, a: list[str]) -> str:
    return _table([f"{i.label}: {i.status}{' — ' + i.last_error if i.last_error else ''}"
                   for i in s.scalars(select(Integration).where(Integration.kind != "social"))])


def c_production(s: Session, a: list[str]) -> str:
    rows = s.scalars(select(Clip).order_by(Clip.id.desc()).limit(10))
    return _table([f"clip {c.id} c#{c.campaign_id} {c.engine} {c.status} QC {c.qc_score}" for c in rows])


def c_queue(s: Session, a: list[str]) -> str:
    rows = s.scalars(select(Job).where(Job.status.in_(["queued", "running"])).order_by(Job.run_at).limit(15))
    return _table([f"job {j.id} {j.kind} {j.status} at {j.run_at:%m-%d %H:%M}" for j in rows])


def c_submissions(s: Session, a: list[str]) -> str:
    rows = s.scalars(select(Submission).order_by(Submission.id.desc()).limit(12))
    return _table([f"#{x.id} c#{x.campaign_id} {x.platform} {x.status} views {x.views or '-'} "
                   f"{x.published_url or ''}" for x in rows])


def c_earnings(s: Session, a: list[str]) -> str:
    m = money(s)
    return (f"ESTIMATED (not money): ${m['estimated']:.2f}\nPending: ${m['pending']:.2f}\nApproved: ${m['approved']:.2f}\n"
            f"Withdrawable: ${m['withdrawable']:.2f}\nPAID (received): ${m['paid']:.2f}")


def c_profit(s: Session, a: list[str]) -> str:
    m = money(s)
    return f"Cash basis: received ${m['paid']:.2f} − costs ${m['costs']:.2f} = ${m['net_profit_cash']:.2f}"


def c_costs(s: Session, a: list[str]) -> str:
    rows = s.execute(select(LedgerEntry.category, LedgerEntry.amount_usd).where(LedgerEntry.kind == "cost")).all()
    agg: dict[str, float] = {}
    for cat, amt in rows:
        agg[cat] = agg.get(cat, 0) + amt
    return _table([f"{k}: ${v:.2f}" for k, v in agg.items()]) + \
        f"\nLimits: ${settings.daily_spend_limit_usd}/day, ${settings.monthly_spend_limit_usd}/month"


def c_strategy(s: Session, a: list[str]) -> str:
    return "\n".join(recommendations(breakdowns(s)))


def c_experiments(s: Session, a: list[str]) -> str:
    return _table([f"#{e.id} [{e.status}] {e.hypothesis}\n   {e.conclusion or 'collecting data'}"
                   for e in s.scalars(select(Experiment))])


def c_dailyplan(s: Session, a: list[str]) -> str:
    sel = list(s.scalars(select(Campaign).where(Campaign.selected)))
    pend = s.scalars(select(Approval).where(Approval.status == "pending")).all()
    waiting = s.scalars(select(Submission).where(Submission.status.in_(["ready_to_publish", "awaiting_submission"]))).all()
    return "\n".join(["Today:", *(f"• produce for #{c.id} {c.name[:40]}" for c in sel),
                      f"• {len(waiting)} posts/submissions waiting", f"• {len(pend)} approvals for you"])


def c_manager(s: Session, a: list[str]) -> str:
    return daily_report(s)


def c_decide(decision: str) -> Callable[[Session, list[str]], str]:
    def f(s: Session, a: list[str]) -> str:
        ap = s.get(Approval, int(a[0])) if a and a[0].isdigit() else None
        if not ap or ap.status != "pending":
            pend = s.scalars(select(Approval).where(Approval.status == "pending")).all()
            return "Pending:\n" + _table([f"#{p.id} {p.kind}: {p.summary}" for p in pend])
        ap.status, ap.decided_at = decision, now()
        audit(s, "owner", f"approval_{decision}", "approval", ap.id, kind=ap.kind)
        return f"Approval #{ap.id} {decision}."
    return f


def c_posted(s: Session, a: list[str]) -> str:
    if len(a) < 2 or not a[0].isdigit() or not a[1].startswith("http"):
        return "usage: /posted <submission_id> <url>"
    sub = s.get(Submission, int(a[0]))
    if sub is None:
        return "unknown submission"
    pipeline.mark_published(s, sub, a[1], "confirmed by owner via Telegram")
    return f"#{sub.id} marked published; submission step queued."


def c_submitted(s: Session, a: list[str]) -> str:
    sub = s.get(Submission, int(a[0])) if a and a[0].isdigit() else None
    if not sub:
        return "usage: /submitted <submission_id>"
    pipeline.mark_submitted(s, sub, "confirmed by owner via Telegram")
    return f"#{sub.id} marked submitted."


def c_rejected(s: Session, a: list[str]) -> str:
    sub = s.get(Submission, int(a[0])) if a and a[0].isdigit() else None
    if not sub or len(a) < 3:
        return f"usage: /rejected <id> <category> <reason>; categories: {', '.join(sorted(pipeline.REJECTION_CATEGORIES))}"
    pipeline.record_rejection(s, sub, a[1], " ".join(a[2:]))
    return f"#{sub.id} rejection recorded; QC rule added."


def c_approved(s: Session, a: list[str]) -> str:
    sub = s.get(Submission, int(a[0])) if a and a[0].isdigit() else None
    if not sub:
        return "usage: /approved <submission_id>"
    sub.status = "approved"
    audit(s, "owner", "marketplace_approved", "submission", sub.id)
    return f"#{sub.id} marked approved by marketplace."


def c_paid(s: Session, a: list[str]) -> str:
    try:
        amt = float(a[0])
    except (IndexError, ValueError):
        return "usage: /paid <amount_usd> <marketplace> [note]"
    s.add(LedgerEntry(kind="revenue", category=f"payout:{a[1] if len(a) > 1 else '?'}", amount_usd=amt,
                      status="paid", note=" ".join(a[2:])))
    audit(s, "owner", "payout_recorded", amount=amt)
    return f"Recorded ${amt:.2f} received."


def c_source(s: Session, a: list[str]) -> str:
    if len(a) < 2 or not a[0].isdigit():
        return "usage: /source <campaign_id> <direct-video-url>"
    enqueue(s, "fetch_source", {"campaign_id": int(a[0]), "url": a[1]})
    return "Source download queued (only use links from the campaign brief)."


def c_state(state: str) -> Callable[[Session, list[str]], str]:
    def f(s: Session, a: list[str]) -> str:
        set_flag(s, "run_state", state)
        audit(s, "owner", f"run_state:{state}", level="warning")
        if state == "stopped":
            for j in s.scalars(select(Job).where(Job.status == "queued")):
                j.status = "cancelled"
        return {"paused": "Paused: no new production/publishing/submission. Monitoring continues.",
                "running": "Resumed.", "stopped": "EMERGENCY STOP: all queued jobs cancelled, worker idle. /resume to restart."}[state]
    return f


COMMANDS: dict[str, Callable[[Session, list[str]], str]] = {
    "status": c_status, "report": c_manager, "campaigns": c_campaigns, "discover": c_discover,
    "opportunities": c_opps, "campaign": c_campaign, "accounts": c_accounts, "integrations": c_integrations,
    "production": c_production, "queue": c_queue, "submissions": c_submissions, "analytics": c_strategy,
    "earnings": c_earnings, "profit": c_profit, "costs": c_costs, "strategy": c_strategy,
    "experiments": c_experiments, "dailyplan": c_dailyplan, "manager": c_manager,
    "pause": c_state("paused"), "resume": c_state("running"), "stopall": c_state("stopped"),
    "approve": c_decide("approved"), "reject": c_decide("rejected"), "help": lambda s, a: HELP,
    "start": lambda s, a: HELP, "posted": c_posted, "submitted": c_submitted, "rejected": c_rejected,
    "approved": c_approved, "paid": c_paid, "source": c_source,
}


def handle_text(text: str, sender_id: str) -> str | None:
    if not settings.telegram_owner_id or str(sender_id) != str(settings.telegram_owner_id):
        with session() as s:
            audit(s, "telegram", "unauthorized_sender", level="warning", sender=str(sender_id))
        return None
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return HELP
    cmd = parts[0][1:].split("@")[0].lower()
    fn = COMMANDS.get(cmd)
    if fn is None:
        return "Unknown command.\n" + HELP
    with session() as s:
        try:
            return fn(s, parts[1:])
        except Exception as e:  # noqa: BLE001
            return f"Error: {str(e)[:300]}"


def run_bot() -> None:
    token = secret("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")
    base = f"https://api.telegram.org/bot{token}"
    offset = 0
    with httpx.Client(timeout=70) as cl:
        while True:
            try:
                r = cl.get(f"{base}/getUpdates", params={"timeout": 60, "offset": offset})
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    reply = handle_text(msg.get("text", ""), str((msg.get("from") or {}).get("id", "")))
                    if reply:
                        for i in range(0, len(reply), 3900):
                            cl.post(f"{base}/sendMessage", json={"chat_id": msg["chat"]["id"], "text": reply[i:i + 3900],
                                                                 "disable_web_page_preview": True})
            except httpx.HTTPError:
                time.sleep(5)
