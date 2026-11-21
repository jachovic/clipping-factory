"""Web dashboard (FastAPI + server-rendered Jinja). Protected by DASHBOARD_TOKEN; bind to localhost or put
behind HTTPS. No secrets are ever sent to the browser."""
from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from .. import campaigns as camp
from ..config import settings
from ..growth import breakdowns, recommendations
from ..integrations import SOCIAL_PUBLISHING
from ..manager import SCHEDULE, overview
from ..models import (Approval, AuditLog, Campaign, CampaignEvent, Clip, ComplianceRule, Experiment, Integration, Job,
                      LedgerEntry, SocialAccount, SourceAsset, Submission)
from ..ops import audit, enqueue, set_flag, session

app = FastAPI(title="Clipping Factory", docs_url=None, redoc_url=None)


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """Block cross-site form posts (a web page you visit must not be able to change settings here)."""
    if request.method == "POST":
        from urllib.parse import urlparse
        src = request.headers.get("origin") or request.headers.get("referer") or ""
        host = request.headers.get("host", "")
        if src and urlparse(src).netloc != host:
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("cross-origin request blocked", status_code=403)
    return await call_next(request)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

SECTIONS = [("setup", "⚙ Konfiguracja"), ("overview", "Overview"), ("manager", "AI Agency Manager"), ("discovery", "Campaign Discovery"),
            ("intelligence", "Campaign Intelligence"), ("profitability", "Campaign Profitability"),
            ("integrations", "Integration Setup"), ("accounts", "Social Accounts"),
            ("account_factory", "Account Factory"), ("videos", "Video Library"), ("factory", "AI Clipping Factory"),
            ("production", "Production Queue"), ("qc", "Quality Control"), ("calendar", "Publishing Calendar"),
            ("submissions", "Campaign Submissions"), ("analytics", "Analytics"), ("earnings", "Earnings & Payouts"),
            ("experiments", "Experiments"), ("automation", "Automation"), ("costs", "Software Costs"),
            ("logs", "Logs"), ("settings", "Settings")]


def auth(request: Request) -> None:
    tok = settings.dashboard_token
    if not tok:
        if request.client and request.client.host in ("127.0.0.1", "::1", "testclient"):
            return
        raise HTTPException(403, "Set DASHBOARD_TOKEN to access the dashboard remotely")
    given = request.cookies.get("ftoken") or request.query_params.get("token") or ""
    if not hmac.compare_digest(given, tok):
        raise HTTPException(401, "Unauthorized")


def usd(v: float | None) -> str:
    v = v or 0.0
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def T(title: str, cols: list[str], rows: list[list[Any]], note: str = "") -> dict[str, Any]:
    return {"title": title, "cols": cols, "rows": rows, "note": note}


def build(section: str, msg: str = "") -> dict[str, Any]:
    if section == "setup":
        from .setup_page import render
        with session() as s:
            o = overview(s)
        return {"kpis": [], "tables": [], "html": render(msg), "run_state": o["run_state"],
                "pending_approvals": o["pending_approvals"]}
    with session() as s:
        o = overview(s)
        ctx: dict[str, Any] = {"kpis": [], "tables": [], "html": ""}
        camps = list(s.scalars(select(Campaign).order_by(Campaign.score.desc().nullslast())))
        if section == "overview":
            ctx["kpis"] = [("Active campaigns", o["campaigns_active"]), ("New 24h", o["campaigns_new_24h"]),
                           ("Selected", o["campaigns_selected"]), ("Clips produced", o["clips_produced"]),
                           ("Published", o["published"]), ("Submitted", o["submitted"]), ("Approved", o["approved"]),
                           ("Rejected", o["rejected"]), ("Public views", f"{o['views_public']:,}"),
                           ("Eligible views ✔", f"{o['views_eligible']:,}")]
            ctx["money"] = [("Estimated (not money)", o["usd_estimated"], "est"), ("Pending", o["usd_pending"], "est"),
                            ("Approved", o["usd_approved"], "conf"), ("Withdrawable", o["usd_withdrawable"], "conf"),
                            ("PAID — received", o["usd_paid"], "conf"), ("Costs", o["usd_costs"], "cost"),
                            ("Net profit (cash)", o["usd_net_profit_cash"], "conf")]
            ctx["tables"].append(T("Top opportunities", ["#", "Campaign", "Marketplace", "Rate", "Score", "Exp. net/h"],
                                   [[r["id"], r["name"], r["marketplace"], r["rate"], r["score"], r["expected_net_per_hour"]]
                                    for r in camp.recommend_allocation(s, 8)]))
        elif section == "manager":
            from ..manager import daily_report
            ctx["html"] = f"<pre>{daily_report(s)}</pre>"
            ctx["tables"].append(T("Approvals", ["#", "Kind", "Summary", "Status", "Action"],
                                   [[a.id, a.kind, a.summary, a.status, f"approval:{a.id}" if a.status == "pending" else ""]
                                    for a in s.scalars(select(Approval).order_by(Approval.id.desc()).limit(30))]))
        elif section == "discovery":
            ev = s.scalars(select(CampaignEvent).order_by(CampaignEvent.id.desc()).limit(60))
            ctx["tables"].append(T("Campaign events", ["When", "Campaign", "Kind", "Detail"],
                                   [[e.at.strftime("%m-%d %H:%M"), e.campaign_id, e.kind, str(e.detail)[:120]] for e in ev]))
            ctx["actions"] = ["discover"]
        elif section in ("intelligence",):
            ctx["tables"].append(T("Campaigns", ["#", "Pri", "Score", "Name", "Brand", "Mkt", "Rate", "Min/Max per video",
                                                 "Budget left", "Platforms", "Lang", "App?", "Brief", "Status", "Link"],
                                   [[c.id, c.priority, c.score, c.name[:60], c.brand, c.marketplace, camp.fmt_rate(c),
                                     f"{c.min_payout_usd or '-'} / {c.max_payout_per_video_usd or '-'}",
                                     c.budget_available_usd, ",".join(c.platforms or []), c.language or "?",
                                     "yes" if c.requires_application else "", "full" if c.brief_complete else "partial",
                                     c.status, f"link:{c.url}"] for c in camps]))
        elif section == "profitability":
            rows = []
            for c in camps:
                p = c.profitability or {}
                if c.status != "active" or not p:
                    continue
                rows.append([c.id, c.name[:50], camp.fmt_rate(c)] +
                            [usd((p.get(k) or {}).get("net_usd", 0)) for k in ("worst", "conservative", "expected", "high")] +
                            [(p.get("expected") or {}).get("net_per_hour_usd"), p.get("min_views_to_earn", "-"),
                             p.get("views_at_cap", "-")])
            ctx["tables"].append(T("Scenarios for 6 clips (priors until we have our own data)",
                                   ["#", "Campaign", "Rate", "Worst", "Conservative", "Expected", "High", "Exp. net/h",
                                    "Views to earn", "Views at cap"], rows,
                                   "Estimates only — never counted as money."))
        elif section == "integrations":
            ctx["tables"].append(T("Integrations", ["Service", "Kind", "Status", "Auth", "Identifier", "Operations",
                                                    "Last OK", "Error", "Manual action"],
                                   [[i.label, i.kind, i.status, i.auth_method, i.account_identifier,
                                     ", ".join(f"{k}:{v}" for k, v in (i.operations or {}).items()), i.last_test_at,
                                     i.last_error, i.manual_action if i.status != "connected" else ""]
                                    for i in s.scalars(select(Integration).order_by(Integration.kind, Integration.label))]))
        elif section == "accounts":
            ctx["tables"].append(T("Social accounts", ["Label", "Platform", "Username", "Status", "Auth", "Publish",
                                                       "Lang", "Niche", "Followers", "Avg views", "Health", "Linked on"],
                                   [[a.label, a.platform, a.username, a.status, a.auth_status, a.publish_method, a.language,
                                     a.niche, a.followers, a.avg_views, a.health, ",".join(a.marketplaces or [])]
                                    for a in s.scalars(select(SocialAccount).order_by(SocialAccount.platform, SocialAccount.label))]))
            ctx["tables"].append(T("Official publishing APIs", ["Platform", "API", "Auth", "Constraint"],
                                   [[k, v["api"], v["auth"], v["notes"]] for k, v in SOCIAL_PUBLISHING.items()]))
        elif section == "account_factory":
            ctx["html"] = ("<p>Additional accounts are only recommended when they have a distinct niche/language and are "
                           "allowed by the platform and the campaigns. Real people create and verify accounts through "
                           "each platform's normal sign-up; the factory only prepares names, bios and content plans.</p>"
                           "<p>Generate a kit: <code>factory account-kit --platform tiktok --niche gaming --language en</code></p>")
        elif section == "videos":
            ctx["tables"].append(T("Source assets", ["#", "Campaign", "File", "Duration", "Rights", "Words"],
                                   [[x.id, x.campaign_id, Path(x.path).name, f"{x.duration_s or 0:.0f}s", x.rights[:90],
                                     len(x.transcript.get("words", []))] for x in s.scalars(select(SourceAsset))]))
        elif section in ("factory", "qc"):
            clips = list(s.scalars(select(Clip).order_by(Clip.id.desc()).limit(100)))
            ctx["tables"].append(T("Clips", ["#", "Campaign", "Engine", "Range", "Moment", "QC", "Decision", "Status",
                                             "Hook", "Failed checks", "Preview"],
                                   [[c.id, c.campaign_id, c.engine, f"{c.start_s:.0f}-{c.end_s:.0f}s", c.moment_score,
                                     c.qc_score, (c.qc_report or {}).get("decision"), c.status, c.hook[:60],
                                     ", ".join(x["check"] for x in (c.qc_report or {}).get("checks", []) if not x["ok"]),
                                     f"clip:{c.id}" if c.path else ""] for c in clips]))
            ctx["tables"].append(T("Learned compliance rules", ["#", "Marketplace", "Campaign", "Category", "Rule"],
                                   [[r.id, r.marketplace, r.campaign_id, r.category, r.rule]
                                    for r in s.scalars(select(ComplianceRule))]))
        elif section in ("production", "automation"):
            jobs = s.scalars(select(Job).order_by(Job.id.desc()).limit(80))
            if section == "automation":
                ctx["html"] = (f"<p>Run state: <b>{o['run_state']}</b>. Periodic jobs (minutes): {SCHEDULE}. "
                               "Daily report 07:00 UTC.</p>")
                ctx["actions"] = ["running", "paused", "stopped"]
            ctx["tables"].append(T("Jobs", ["#", "Kind", "Status", "Run at", "Attempts", "Error"],
                                   [[j.id, j.kind, j.status, j.run_at.strftime("%m-%d %H:%M"), j.attempts, j.last_error[:150]]
                                    for j in jobs if section == "automation" or j.kind in ("produce_clip", "inbox", "fetch_source")]))
        elif section in ("calendar", "submissions"):
            subs = s.scalars(select(Submission).order_by(Submission.scheduled_for if section == "calendar" else Submission.id.desc()))
            ctx["tables"].append(T("Submissions", ["#", "Campaign", "Account", "Platform", "Status", "Scheduled",
                                                   "Published URL", "Deadline", "Submitted", "Views", "Eligible", "Est. $",
                                                   "Approved $", "Paid $", "Rejection"],
                                   [[x.id, x.campaign_id, x.account_id, x.platform, x.status, x.scheduled_for,
                                     f"link:{x.published_url}" if x.published_url else "", x.submission_deadline_at,
                                     x.submitted_at, x.views, x.eligible_views, x.earnings_estimated_usd,
                                     x.earnings_approved_usd, x.earnings_paid_usd,
                                     f"{x.rejection_category}: {x.rejection_reason}" if x.rejection_reason else ""] for x in subs]))
        elif section == "analytics":
            b = breakdowns(s)
            ctx["html"] = "<ul>" + "".join(f"<li>{r}</li>" for r in recommendations(b)) + "</ul>"
            for dim, g in b.items():
                ctx["tables"].append(T(f"By {dim}", ["Group", "Posts", "Median views", "Enough data"],
                                       [[k, v["n"], v["median_views"], v["enough_data"]] for k, v in g.items()]))
        elif section in ("earnings", "costs"):
            kind = "revenue" if section == "earnings" else "cost"
            ctx["tables"].append(T("Ledger", ["When", "Category", "Amount $", "Status", "Campaign", "Note"],
                                   [[e.at.strftime("%Y-%m-%d"), e.category, e.amount_usd, e.status, e.campaign_id, e.note]
                                    for e in s.scalars(select(LedgerEntry).where(LedgerEntry.kind == kind)
                                                       .order_by(LedgerEntry.id.desc()))],
                                   "Only status 'paid' revenue is money received." if kind == "revenue" else
                                   f"Limits: ${settings.daily_spend_limit_usd}/day · ${settings.monthly_spend_limit_usd}/month"))
        elif section == "experiments":
            ctx["tables"].append(T("Experiments", ["#", "Status", "Hypothesis", "Variable", "Arms", "Results",
                                                   "Conclusion", "Next action"],
                                   [[e.id, e.status, e.hypothesis, e.variable, str(e.arms), str(e.results)[:160],
                                     e.conclusion, e.next_action] for e in s.scalars(select(Experiment))]))
        elif section == "logs":
            ctx["tables"].append(T("Audit log", ["When", "Level", "Actor", "Action", "Entity", "Detail"],
                                   [[l.at.strftime("%m-%d %H:%M:%S"), l.level, l.actor, l.action,
                                     f"{l.entity}#{l.entity_id}", str(l.detail)[:200]]
                                    for l in s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200))]))
        elif section == "settings":
            ctx["html"] = (f"<p>Database: {'PostgreSQL' if 'postgres' in settings.db_url else 'SQLite'} · Whisper model "
                           f"{settings.whisper_model} · Crayo test cap {settings.crayo_test_clip_cap} clips · spend limits "
                           f"${settings.daily_spend_limit_usd}/day, ${settings.monthly_spend_limit_usd}/month.</p>"
                           "<p>Secrets live only in the server's <code>.env</code> (chmod 600) and are never shown here.</p>")
        ctx["run_state"] = o["run_state"]
        ctx["pending_approvals"] = o["pending_approvals"]
        return ctx


@app.get("/login")
def login(token: str):
    if not settings.dashboard_token or not hmac.compare_digest(token, settings.dashboard_token):
        raise HTTPException(401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("ftoken", token, httponly=True, samesite="strict", secure=True, max_age=30 * 86400)
    return r


@app.get("/", response_class=HTMLResponse)
@app.get("/s/{section}", response_class=HTMLResponse)
def page(request: Request, section: str = "overview", _: None = Depends(auth)):
    if section not in dict(SECTIONS):
        raise HTTPException(404)
    ctx = build(section, request.query_params.get("msg", "")[:500])
    if section == "overview":
        from .setup_page import missing_essentials
        todo = missing_essentials()
        if todo:
            ctx["html"] = (f"<b>Konfiguracja niedokończona:</b> {', '.join(todo)}. "
                           "<a href='/s/setup'>Przejdź do Konfiguracji →</a>") + ctx.get("html", "")
    return templates.TemplateResponse(request, "page.html", {"sections": SECTIONS, "section": section,
                                                             "title": dict(SECTIONS)[section], **ctx})


@app.post("/action/state")
def action_state(state: str = Form(...), _: None = Depends(auth)):
    if state not in ("running", "paused", "stopped"):
        raise HTTPException(400)
    with session() as s:
        set_flag(s, "run_state", state)
        audit(s, "owner", f"run_state:{state}", level="warning", via="dashboard")
    return RedirectResponse("/s/automation", status_code=303)


@app.post("/action/discover")
def action_discover(_: None = Depends(auth)):
    from ..models import now
    with session() as s:
        enqueue(s, "discover", {}, dedupe_key=f"discover:dash:{now():%Y%m%d%H%M}")
    return RedirectResponse("/s/discovery", status_code=303)


@app.post("/action/approval/{aid}")
def action_approval(aid: int, decision: str = Form(...), _: None = Depends(auth)):
    from ..models import now
    with session() as s:
        a = s.get(Approval, aid)
        if a and a.status == "pending" and decision in ("approved", "rejected"):
            a.status, a.decided_at = decision, now()
            audit(s, "owner", f"approval_{decision}", "approval", aid, via="dashboard")
    return RedirectResponse("/s/manager", status_code=303)


@app.get("/clip/{cid}.mp4")
def clip_file(cid: int, _: None = Depends(auth)):
    with session() as s:
        c = s.get(Clip, cid)
        if not c or not c.path:
            raise HTTPException(404)
        p = Path(c.path).resolve()
    if settings.data_dir.resolve() not in p.parents:
        raise HTTPException(403)
    return FileResponse(p, media_type="video/mp4")


@app.get("/healthz")
def healthz():
    with session() as s:
        o = overview(s)
    return {"ok": True, "run_state": o["run_state"], "jobs_queued": o["jobs_queued"]}


from .setup_page import router as _setup_router  # noqa: E402

app.include_router(_setup_router)
