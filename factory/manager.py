"""Central autonomous manager: DISCOVER → ANALYZE → SELECT → PRODUCE → QC → PUBLISH → SUBMIT → MONITOR → OPTIMIZE.
Runs as job handlers inside the worker; the scheduler below enqueues periodic work with time-bucketed
dedupe keys so restarts never double-run a cycle."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import campaigns as camp
from . import pipeline
from .accounts import assign_accounts
from .config import settings
from .growth import growth_review
from .integrations import test_all, write_status_md
from .models import (Approval, Campaign, Clip, Job, LedgerEntry, SourceAsset, Submission, now)
from .notify import notify
from .ops import audit, claim_next_job, enqueue, recover_stale_jobs, run_job, run_state, session
from .production import ingest_source

MAX_SELECTED = 4          # campaigns we actively produce for at once (scale after the first loop works)
INBOX = settings.data_dir / "inbox"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


# ------------------------------------------------------------------ handlers
def h_discover(s: Session, job: Job) -> dict:
    res = camp.discover_all(s)
    h_select(s, job)
    return res


def h_select(s: Session, job: Job) -> dict:
    """Pick the best HIGH-priority campaigns we are eligible for; ask for source material."""
    selected = list(s.scalars(select(Campaign).where(Campaign.selected, Campaign.status == "active")))
    for c in s.scalars(select(Campaign).where(Campaign.selected, Campaign.status != "active")):
        c.selected = False
    picked = []
    for c in camp.recommend_allocation(s, top=20):
        if len(selected) + len(picked) >= MAX_SELECTED:
            break
        row = s.get(Campaign, c["id"])
        if row.selected or row.priority != "HIGH":
            continue
        if not assign_accounts(s, row):
            continue
        row.selected = True
        picked.append(row)
        (INBOX / str(row.id)).mkdir(parents=True, exist_ok=True)
        audit(s, "manager", "campaign_selected", "campaign", row.id, score=row.score)
        notify(s, "new_campaign", f"Selected campaign {row.id}: {row.name} ({row.marketplace}, {camp.fmt_rate(row)}).\n"
                                  f"1) Join it: {row.url}\n"
                                  + (f"Footage named in the brief: {', '.join(row.source_links[:5])}\n" if row.source_links else "")
                                  + f"2) Put the official source video(s) from its brief into "
                                  f"data/inbox/{row.id}/ (or /source {row.id} <direct-url>).")
    return {"selected": [c.id for c in picked]}


def h_inbox(s: Session, job: Job) -> dict:
    """Ingest operator-supplied source files. Placing a file in a campaign's inbox folder is the operator's
    statement that it is the campaign-provided material."""
    n = 0
    if not INBOX.exists():
        return {"ingested": 0}
    for d in INBOX.iterdir():
        if not d.is_dir() or not d.name.isdigit():
            continue
        c = s.get(Campaign, int(d.name))
        if c is None:
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in VIDEO_EXT or f.name.startswith("."):
                continue
            rights = (d / "RIGHTS.txt").read_text().strip() if (d / "RIGHTS.txt").exists() else \
                f"Official source material from campaign brief {c.url}, supplied by operator for this campaign."
            src = ingest_source(s, f, rights=rights, campaign_id=c.id)
            done = d / "ingested"
            done.mkdir(exist_ok=True)
            target = done / f.name
            k = 1
            while target.exists():          # Windows cannot rename onto an existing file
                target = done / f"{f.stem}-{k}{f.suffix}"
                k += 1
            f.rename(target)
            src.path = str(target)
            enqueue(s, "produce_clip", {"campaign_id": c.id, "source_id": src.id, "n": 2},
                    dedupe_key=f"produce:{c.id}:{src.id}")
            n += 1
    return {"ingested": n}


def direct_download_url(url: str) -> str:
    import re
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([\w-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}&confirm=t"
    if "dropbox.com" in url:
        return re.sub(r"([?&])dl=0", r"\1dl=1", url) if "dl=0" in url else url + ("&" if "?" in url else "?") + "dl=1"
    return url


def h_fetch_source(s: Session, job: Job) -> dict:
    """Download official campaign material into the campaign inbox (then h_inbox ingests it)."""
    import httpx
    c = s.get(Campaign, job.payload["campaign_id"])
    url = job.payload["url"]
    dest_dir = INBOX / str(c.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "RIGHTS.txt").write_text(f"Official source material for campaign '{c.name}' ({c.url}); "
                                         f"link supplied by operator from the campaign brief: {url}")
    tmp = dest_dir / f".download-{job.id}.part"
    with httpx.stream("GET", direct_download_url(url), follow_redirects=True, timeout=1800) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "text/html" in ctype:
            raise RuntimeError("link returned a web page, not a video file — use a direct/shared file link")
        size = 0
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                size += len(chunk)
                if size > 4 * 1024 ** 3:
                    raise RuntimeError("file larger than 4 GB")
                fh.write(chunk)
    final = dest_dir / f"source-{job.id}.mp4"
    tmp.rename(final)
    enqueue(s, "inbox", {}, dedupe_key=f"inbox:after-fetch:{job.id}")
    return {"file": str(final), "bytes": size}


def h_produce(s: Session, job: Job) -> dict:
    p = job.payload
    return pipeline.produce(s, p["campaign_id"], p["source_id"], p.get("n", 2))


def h_publish(s: Session, job: Job) -> dict:
    return pipeline.publish(s, job.payload["submission_id"])


def h_submit(s: Session, job: Job) -> dict:
    return pipeline.submit(s, job.payload["submission_id"])


def h_deadlines(s: Session, job: Job) -> dict:
    return {"warned": pipeline.check_deadlines(s)}


def h_metrics(s: Session, job: Job) -> dict:
    return {"refreshed": pipeline.refresh_metrics(s)}


def h_report(s: Session, job: Job) -> dict:
    text = daily_report(s)
    notify(s, "daily_report", text)
    return {"chars": len(text)}


def h_integrations(s: Session, job: Job) -> dict:
    res = test_all(s)
    write_status_md(s)
    return res


def h_growth(s: Session, job: Job) -> dict:
    return growth_review(s)


def h_backup(s: Session, job: Job) -> dict:
    from .backup import backup_db
    return {"file": str(backup_db())}


HANDLERS = {"discover": h_discover, "select": h_select, "inbox": h_inbox, "produce_clip": h_produce,
            "publish": h_publish, "submit": h_submit, "deadlines": h_deadlines, "metrics": h_metrics,
            "daily_report": h_report, "integrations": h_integrations, "growth": h_growth, "backup": h_backup,
            "fetch_source": h_fetch_source}

# kind -> period in minutes
SCHEDULE = {"discover": 120, "inbox": 5, "deadlines": 15, "metrics": 360, "integrations": 720,
            "growth": 1440, "backup": 1440, "subscriptions": 720}
DAILY_REPORT_HOUR_UTC = 7


def schedule_periodic(s: Session) -> None:
    t = now()
    for kind, period in SCHEDULE.items():
        bucket = int(t.timestamp() // (period * 60))
        enqueue(s, kind, {}, dedupe_key=f"{kind}:{bucket}", max_attempts=2)
    if t.hour == DAILY_REPORT_HOUR_UTC:
        enqueue(s, "daily_report", {}, dedupe_key=f"daily_report:{t:%Y-%m-%d}")
        if t.weekday() == 0:
            enqueue(s, "period_report", {"days": 7}, dedupe_key=f"weekly_report:{t:%Y-%m-%d}")
        if t.day == 1:
            enqueue(s, "period_report", {"days": 30}, dedupe_key=f"monthly_report:{t:%Y-%m}")


def worker_loop(once: bool = False, idle_sleep: float = 5.0) -> None:
    with session() as s:
        n = recover_stale_jobs(s)
        if n:
            audit(s, "worker", "recovered_stale_jobs", count=n)
    last_sched = 0.0
    while True:
        if time.monotonic() - last_sched > 60:
            with session() as s:
                if run_state(s) != "stopped":
                    schedule_periodic(s)
                recover_stale_jobs(s)
            last_sched = time.monotonic()
        with session() as s:
            job = claim_next_job(s)
            job_id = job.id if job else None
        if job_id:
            run_job(job_id, HANDLERS)
            continue
        if once:
            return
        time.sleep(idle_sleep)


# ------------------------------------------------------------------- reports
def money(s: Session) -> dict[str, float]:
    q = lambda col: float(s.scalar(select(func.coalesce(func.sum(col), 0.0))) or 0)  # noqa: E731
    costs = float(s.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0.0))
                           .where(LedgerEntry.kind == "cost")) or 0)
    paid_ledger = float(s.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0.0))
                                 .where(LedgerEntry.kind == "revenue", LedgerEntry.status == "paid")) or 0)
    withdrawable = float(s.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0.0))
                                  .where(LedgerEntry.kind == "revenue", LedgerEntry.status == "withdrawable")) or 0)
    return {"estimated": q(Submission.earnings_estimated_usd), "pending": q(Submission.earnings_pending_usd),
            "approved": q(Submission.earnings_approved_usd), "withdrawable": withdrawable,
            "paid": paid_ledger, "costs": costs, "net_profit_cash": paid_ledger - costs}


def overview(s: Session) -> dict[str, Any]:
    cnt = lambda *w: s.scalar(select(func.count()).select_from(w[0]).where(*w[1:])) or 0  # noqa: E731
    day_ago = now() - dt.timedelta(days=1)
    m = money(s)
    return {
        "run_state": run_state(s),
        "campaigns_active": cnt(Campaign, Campaign.status == "active"),
        "campaigns_new_24h": cnt(Campaign, Campaign.first_seen_at >= day_ago),
        "campaigns_high": cnt(Campaign, Campaign.status == "active", Campaign.priority == "HIGH"),
        "campaigns_selected": cnt(Campaign, Campaign.selected),
        "clips_produced": cnt(Clip, Clip.status != "failed"),
        "clips_qc_approved": cnt(Clip, Clip.status.in_(["qc_approved", "ready_to_publish", "published"])),
        "published": cnt(Submission, Submission.published_url.is_not(None)),
        "submitted": cnt(Submission, Submission.submitted_at.is_not(None)),
        "approved": cnt(Submission, Submission.status.in_(["approved", "earning", "pending_payout", "paid"])),
        "rejected": cnt(Submission, Submission.status == "rejected"),
        "views_public": int(s.scalar(select(func.coalesce(func.sum(Submission.views), 0))) or 0),
        "views_eligible": int(s.scalar(select(func.coalesce(func.sum(Submission.eligible_views), 0))) or 0),
        "pending_approvals": cnt(Approval, Approval.status == "pending"),
        "jobs_failed_24h": cnt(Job, Job.status == "failed", Job.finished_at >= day_ago),
        "jobs_queued": cnt(Job, Job.status == "queued"),
        **{f"usd_{k}": round(v, 2) for k, v in m.items()},
    }


def cost_metrics(s: Session, since: dt.datetime | None = None) -> dict[str, Any]:
    """Unit economics. Revenue uses ONLY money received (ledger 'paid')."""
    def total(kind: str, status: str | None = None, campaign_id: int | None = None) -> float:
        q = select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0.0)).where(LedgerEntry.kind == kind)
        if status:
            q = q.where(LedgerEntry.status == status)
        if since:
            q = q.where(LedgerEntry.at >= since)
        if campaign_id is not None:
            q = q.where(LedgerEntry.campaign_id == campaign_id)
        return float(s.scalar(q) or 0)
    clip_q = select(func.count()).select_from(Clip).where(Clip.status != "failed")
    appr_q = select(func.count()).select_from(Submission).where(
        Submission.status.in_(["approved", "earning", "pending_payout", "paid"]))
    elig_q = select(func.coalesce(func.sum(Submission.eligible_views), 0))
    if since:
        clip_q = clip_q.where(Clip.created_at >= since)
        appr_q = appr_q.where(Submission.created_at >= since)
        elig_q = elig_q.where(Submission.created_at >= since)
    clips, approved, eligible = s.scalar(clip_q) or 0, s.scalar(appr_q) or 0, int(s.scalar(elig_q) or 0)
    costs, received = total("cost"), total("revenue", "paid")
    per_campaign = []
    for c in s.scalars(select(Campaign).where(Campaign.id.in_(select(LedgerEntry.campaign_id).distinct()))):
        cc, rc = total("cost", campaign_id=c.id), total("revenue", "paid", campaign_id=c.id)
        per_campaign.append({"id": c.id, "name": c.name[:40], "costs": round(cc, 2), "received": round(rc, 2),
                             "net": round(rc - cc, 2)})
    div = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {"costs": round(costs, 2), "received": round(received, 2), "net": round(received - costs, 2),
            "clips": clips, "approved_clips": approved, "eligible_views": eligible,
            "cost_per_clip": div(costs, clips), "cost_per_approved_clip": div(costs, approved),
            "cost_per_1k_eligible_views": div(costs * 1000, eligible), "per_campaign": per_campaign}


def period_report(s: Session, days: int) -> str:
    since = now() - dt.timedelta(days=days)
    m = cost_metrics(s, since)
    cnt = lambda *w: s.scalar(select(func.count()).select_from(w[0]).where(*w[1:])) or 0  # noqa: E731
    label = {7: "Weekly", 30: "Monthly"}.get(days, f"{days}-day")
    lines = [f"{label} report — last {days} days",
             f"New campaigns: {cnt(Campaign, Campaign.first_seen_at >= since)} · clips: {m['clips']} · "
             f"published: {cnt(Submission, Submission.published_at >= since)} · approved: {m['approved_clips']} · "
             f"rejected: {cnt(Submission, Submission.status == 'rejected', Submission.updated_at >= since)}",
             f"Eligible views: {m['eligible_views']:,}",
             f"Cash: received ${m['received']:.2f} − costs ${m['costs']:.2f} = net ${m['net']:.2f}",
             f"Cost/clip {m['cost_per_clip']} · cost/approved clip {m['cost_per_approved_clip']} · "
             f"cost per 1k eligible views {m['cost_per_1k_eligible_views']}"]
    lines += [f" • #{p['id']} {p['name']}: net ${p['net']}" for p in m["per_campaign"][:10]]
    from .growth import breakdowns, recommendations
    lines += ["Growth:"] + [f" • {r}" for r in recommendations(breakdowns(s))]
    return "\n".join(lines)


def h_period_report(s: Session, job: Job) -> dict:
    days = int(job.payload.get("days", 7))
    notify(s, "daily_report", period_report(s, days))
    return {"days": days}


def h_subscriptions(s: Session, job: Job) -> dict:
    """Book each configured monthly subscription once per month (setting 'subscriptions' = JSON {name: usd})."""
    import json as _json
    from .ops import get_flag
    subs = _json.loads(get_flag(s, "subscriptions", "{}") or "{}")
    month = now().strftime("%Y-%m")
    booked = []
    for name, usd in subs.items():
        note = f"{name} {month}"
        exists = s.scalar(select(LedgerEntry).where(LedgerEntry.category == "subscription", LedgerEntry.note == note))
        if not exists:
            s.add(LedgerEntry(kind="cost", category="subscription", amount_usd=float(usd), status="paid", note=note))
            booked.append(name)
    return {"booked": booked}


def daily_report(s: Session) -> str:
    o = overview(s)
    top = camp.recommend_allocation(s, top=3)
    lines = [f"Daily report {now():%Y-%m-%d} — state: {o['run_state']}",
             f"Campaigns: {o['campaigns_active']} active ({o['campaigns_new_24h']} new/24h, {o['campaigns_high']} HIGH, "
             f"{o['campaigns_selected']} selected)",
             f"Clips: {o['clips_produced']} produced, {o['clips_qc_approved']} QC-approved",
             f"Posts: {o['published']} published, {o['submitted']} submitted, {o['approved']} approved, {o['rejected']} rejected",
             f"Views: {o['views_public']:,} public / {o['views_eligible']:,} eligible (marketplace-confirmed)",
             f"Money (USD): ESTIMATED {o['usd_estimated']:.2f} | pending {o['usd_pending']:.2f} | approved "
             f"{o['usd_approved']:.2f} | withdrawable {o['usd_withdrawable']:.2f} | PAID {o['usd_paid']:.2f} | "
             f"costs {o['usd_costs']:.2f} | cash net {o['usd_net_profit_cash']:.2f}",
             f"Approvals waiting: {o['pending_approvals']} · failed jobs 24h: {o['jobs_failed_24h']}",
             "Top opportunities:"]
    lines += [f" • #{t['id']} {t['name'][:50]} [{t['marketplace']}] {t['rate']} score {t['score']:.0f}" for t in top]
    return "\n".join(lines)


HANDLERS.update({"period_report": h_period_report, "subscriptions": h_subscriptions})
