"""Production → publishing → submission → monitoring, with every state change persisted."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import production, qc
from .accounts import account_has_capacity, assign_accounts, next_slot
from .campaigns import clip_gross
from .config import secret_present, settings
from .marketplaces import ADAPTERS, get_adapter
from .models import (Assignment, Campaign, Clip, ComplianceRule, MetricSnapshot, SocialAccount, SourceAsset,
                     Submission, now)
from .notify import notify
from .ops import (RetryLater, audit, check_spend, enqueue, get_flag, is_approved, record_cost,
                  request_approval)
from .publishing import get_publisher

# ================================================================ production
CRAYO_SCALE_APPROVAL = "Crayo: allow production beyond the first test clips"


def crayo_allowed(s: Session, clips_wanted: int) -> tuple[bool, str]:
    if not secret_present("CRAYO_API_KEY"):
        return False, "no CRAYO_API_KEY"
    if get_flag(s, "engine_preference", "crayo") == "local":
        return False, "operator prefers local engine"
    made = s.scalar(select(func.count()).select_from(Clip).where(Clip.engine == "crayo")) or 0
    if made + clips_wanted > settings.crayo_test_clip_cap and not is_approved(s, "crayo_scale", CRAYO_SCALE_APPROVAL):
        if made >= settings.crayo_test_clip_cap:
            request_approval(s, "crayo_scale", CRAYO_SCALE_APPROVAL,
                             {"clips_made": made, "cap": settings.crayo_test_clip_cap})
            return False, "test cap reached; awaiting approval"
    return True, ""


def produce(s: Session, campaign_id: int, source_id: int, n: int = 2) -> dict[str, Any]:
    c = s.get(Campaign, campaign_id)
    src = s.get(SourceAsset, source_id)
    if c.status != "active":
        return {"skipped": f"campaign {c.status}"}
    ok, why = crayo_allowed(s, n)
    if ok:
        try:
            clips = produce_crayo(s, c, src, n=max(2, min(n, settings.crayo_test_clip_cap)))
        except Exception as e:  # noqa: BLE001 — fall back, never stall the factory
            audit(s, "production", "crayo_failed_fallback_local", "campaign", c.id, level="warning", error=str(e)[:300])
            notify(s, "production_failed", f"Crayo failed for campaign {c.id} ({str(e)[:150]}); using local engine.")
            clips = production.produce_local(s, src, c, n=n)
    else:
        audit(s, "production", "engine_local", "campaign", c.id, reason=why)
        clips = production.produce_local(s, src, c, n=n)
    results = []
    for clip in clips:
        if clip.status == "failed":
            results.append({"clip": clip.id, "status": "failed"})
            continue
        clip.caption = qc.build_caption(clip, c)
        report = qc.run_qc(s, clip)
        audit(s, "qc", f"qc_{report['decision']}", "clip", clip.id, score=report["score"],
              failures=report["mandatory_failures"])
        results.append({"clip": clip.id, "qc": report["score"], "decision": report["decision"]})
        if clip.status == "qc_approved":
            plan_submissions(s, clip)
    return {"clips": results}


def produce_crayo(s: Session, c: Campaign, src: SourceAsset, n: int) -> list[Clip]:
    from .crayo import CrayoClient, estimate_autoclip, usd_per_credit
    est = estimate_autoclip(n, 60)
    check_spend(s, est["usd"], "crayo autoclip")
    cr = CrayoClient()
    before = cr.account()["credits"]
    asset = cr.upload_file(src.path)
    job = cr.autoclip(asset["id"], clip_count=n, clip_length=60, edit_level="full",
                      language=c.language or None,
                      prompt=f"Standalone moments for the '{c.name}' campaign; complete sentences; strong opening.")
    import time
    t0 = time.monotonic()
    while True:
        j = cr.autoclip_job(job["id"])
        if j["status"] in ("completed", "failed") or time.monotonic() - t0 > 3600:
            break
        time.sleep(15)
    if j["status"] != "completed":
        raise RuntimeError(f"autoclip {j['status']}")
    after = cr.account()["credits"]
    spent = {k: before[k] - after[k] for k in before if before[k] != after[k]}
    audit(s, "crayo", "autoclip_credits", "campaign", c.id, credits_delta=spent)
    out = []
    for item in j["clips"][:n]:
        exp = cr.export(item["project_id"])
        res = cr.wait_export(exp["id"])
        dst = settings.data_dir / "clips" / f"c{c.id}" / f"crayo_{item['project_id']}.mp4"
        dst.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", res["asset"]["url"], timeout=600) as r:
            r.raise_for_status()
            with dst.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        cost = exp.get("credits_charged", 0) * usd_per_credit("export")
        clip = Clip(campaign_id=c.id, source_id=src.id, engine="crayo", path=str(dst), hook=item.get("title", ""),
                    text=item.get("title", ""), status="rendered", external_ref=item["project_id"],
                    cost_usd=cost, content_hash=production.visual_fingerprint(dst), moment_score=60)
        s.add(clip)
        s.flush()
        record_cost(s, "crayo_credits", cost, f"export {exp['id']} credits={exp.get('credits_charged')}",
                    campaign_id=c.id, clip_id=clip.id)
        out.append(clip)
    return out


# ================================================================ planning
def plan_submissions(s: Session, clip: Clip) -> list[Submission]:
    """Map an approved clip to assigned accounts. Duplicate protection: one clip per account per campaign,
    and never the same clip on two accounts of the same platform for the same campaign."""
    c = s.get(Campaign, clip.campaign_id)
    if c is None or c.marketplace == "internal_test":
        return []
    assign_accounts(s, c)
    created = []
    for asg in s.scalars(select(Assignment).where(Assignment.campaign_id == c.id, Assignment.active)):
        a = s.get(SocialAccount, asg.account_id)
        same_platform = s.scalar(select(Submission).where(Submission.campaign_id == c.id, Submission.clip_id == clip.id,
                                                          Submission.platform == a.platform))
        if same_platform:
            continue
        exists = s.scalar(select(Submission).where(Submission.campaign_id == c.id, Submission.clip_id == clip.id,
                                                   Submission.account_id == a.id))
        if exists:
            continue
        sub = Submission(campaign_id=c.id, marketplace=c.marketplace, clip_id=clip.id, account_id=a.id,
                         platform=a.platform, status="ready_to_publish", caption=clip.caption,
                         scheduled_for=next_slot(s, a))
        s.add(sub)
        s.flush()
        created.append(sub)
        enqueue(s, "publish", {"submission_id": sub.id}, run_at=sub.scheduled_for, dedupe_key=f"publish:{sub.id}")
        clip.status = "ready_to_publish"
    return created


# ================================================================ publishing
def publish(s: Session, submission_id: int) -> dict[str, Any]:
    sub = s.get(Submission, submission_id)
    if sub.status not in ("ready_to_publish",):
        return {"skipped": sub.status}
    c, clip, a = s.get(Campaign, sub.campaign_id), s.get(Clip, sub.clip_id), s.get(SocialAccount, sub.account_id)
    if c.status != "active":
        sub.status, sub.last_error = "planned", f"campaign {c.status}; not published"
        return {"skipped": "campaign inactive"}
    if not account_has_capacity(s, a):
        raise RetryLater("account daily cap reached", dt.timedelta(hours=6))
    if clip.qc_report.get("decision") not in ("ready", "acceptable"):
        return {"skipped": "clip not QC-approved"}
    if not c.brief_complete and not is_approved(s, "publish_unclear_rules", f"campaign {c.id} rules"):
        request_approval(s, "publish_unclear_rules", f"campaign {c.id} rules",
                         {"campaign": c.name, "url": c.url,
                          "why": "Some campaign rules are only visible after login. Read them, then approve."})
        raise RetryLater("awaiting approval: campaign rules not fully visible", dt.timedelta(hours=1))
    pub = get_publisher(a)
    res = pub.publish(Path(clip.path), sub.caption, a)
    if res.ok and res.url:
        mark_published(s, sub, res.url, res.confirmation)
        return {"published": res.url}
    if res.manual_task:
        sub.last_error = ""
        notify(s, "publish_task", f"PUBLISH #{sub.id} on {a.platform}/{a.username or a.label} for '{c.name}'\n"
                                  f"File: {clip.path}\nCaption:\n{sub.caption}\n\nAfter posting reply: "
                                  f"/posted {sub.id} <post-url>")
        return {"manual": True}
    sub.last_error = res.error
    notify(s, "publish_failed", f"Publishing #{sub.id} failed: {res.error[:200]}")
    raise RuntimeError(res.error)


def mark_published(s: Session, sub: Submission, url: str, confirmation: str = "") -> None:
    dup = s.scalar(select(Submission).where(Submission.published_url == url, Submission.id != sub.id))
    if dup:
        raise ValueError(f"URL already recorded on submission {dup.id}")
    c = s.get(Campaign, sub.campaign_id)
    sub.published_url, sub.published_at, sub.publish_confirmation = url, now(), confirmation
    window = c.submission_window_hours or ADAPTERS[c.marketplace].known_rules.get("submission_window_hours")
    sub.submission_deadline_at = now() + dt.timedelta(hours=window) if window else None
    sub.status = "awaiting_submission"
    clip = s.get(Clip, sub.clip_id)
    clip.status = "published"
    audit(s, "publisher", "published", "submission", sub.id, url=url)
    enqueue(s, "submit", {"submission_id": sub.id}, dedupe_key=f"submit:{sub.id}")


# ================================================================ submission
def submit(s: Session, submission_id: int) -> dict[str, Any]:
    sub = s.get(Submission, submission_id)
    if sub.status != "awaiting_submission":
        return {"skipped": sub.status}
    prior = s.scalar(select(Submission).where(Submission.campaign_id == sub.campaign_id,
                                              Submission.published_url == sub.published_url,
                                              Submission.submitted_at.is_not(None), Submission.id != sub.id))
    if prior:
        sub.status, sub.last_error = "rejected", f"duplicate of submission {prior.id}; not resubmitted"
        return {"skipped": "duplicate"}
    c, a = s.get(Campaign, sub.campaign_id), s.get(SocialAccount, sub.account_id)
    res = get_adapter(c.marketplace).submit(c.external_id, sub.published_url, a.username or a.label)
    if res.ok:
        mark_submitted(s, sub, res.confirmation)
        return {"submitted": res.confirmation}
    deadline = f"{sub.submission_deadline_at:%Y-%m-%d %H:%M} UTC" if sub.submission_deadline_at else "see campaign"
    notify(s, "submission_deadline", f"SUBMIT #{sub.id} — campaign '{c.name}' ({c.marketplace})\n"
                                     f"Account: {a.platform}/{a.username or a.label}\nVideo: {sub.published_url}\n"
                                     f"Submit at: {c.url}\nDeadline: {deadline}\n"
                                     f"{res.error and 'Auto-submit error: ' + res.error[:150]}\n"
                                     f"Then reply: /submitted {sub.id}")
    return {"manual": True, "error": res.error}


def mark_submitted(s: Session, sub: Submission, confirmation: str = "") -> None:
    sub.status, sub.submitted_at, sub.submission_confirmation = "submitted", now(), confirmation
    audit(s, "submissions", "submitted", "submission", sub.id, confirmation=confirmation)


def check_deadlines(s: Session) -> int:
    """Escalate submissions whose deadline is close; never let one expire silently."""
    n = 0
    for sub in s.scalars(select(Submission).where(Submission.status == "awaiting_submission",
                                                  Submission.submission_deadline_at.is_not(None))):
        left = (sub.submission_deadline_at - now()).total_seconds() / 3600
        if left < 0:
            sub.status, sub.last_error = "rejected", "submission deadline expired"
            notify(s, "submission_deadline", f"DEADLINE MISSED for submission #{sub.id} ({sub.published_url}).")
        elif left < 6:
            notify(s, "submission_deadline", f"{left:.1f}h left to submit #{sub.id}: {sub.published_url} — "
                                             f"reply /submitted {sub.id} once done")
            n += 1
    return n


# ================================================================ outcomes
REJECTION_CATEGORIES = {"duration", "content_rules", "wrong_source", "duplicate", "branding", "hashtags",
                        "disclosure", "quality", "views_not_organic", "late", "other"}


def record_rejection(s: Session, sub: Submission, category: str, reason: str) -> None:
    category = category if category in REJECTION_CATEGORIES else "other"
    sub.status, sub.rejection_category, sub.rejection_reason = "rejected", category, reason
    rule = ComplianceRule(marketplace=sub.marketplace, campaign_id=sub.campaign_id, category=category,
                          rule=f"Rejected #{sub.id}: {reason}", source_submission_id=sub.id)
    import re as _re
    m = _re.search(r"(?:max|under|shorter than|less than)\s*(\d+)\s*s", reason, _re.I)
    if category == "duration" and m:
        rule.check = {"type": "max_duration", "value": float(m.group(1))}
    s.add(rule)
    audit(s, "submissions", "rejected", "submission", sub.id, category=category, reason=reason)
    notify(s, "rejected_clip", f"Submission #{sub.id} rejected ({category}): {reason}. Rule recorded for future QC.")


def refresh_metrics(s: Session) -> int:
    """Public view counts via Crayo View Tracker (1 tracker credit per lookup). Public views are NOT
    eligible views; only marketplace-reported figures go into eligible_views/approved earnings."""
    if not secret_present("CRAYO_API_KEY"):
        return 0
    from .crayo import CrayoClient
    cr, n = CrayoClient(), 0
    platform_map = {"x": "twitter"}
    for sub in s.scalars(select(Submission).where(Submission.published_url.is_not(None),
                                                  Submission.status.in_(["published", "awaiting_submission", "submitted",
                                                                         "under_review", "approved", "earning"]))):
        try:
            post = cr.post_stats(platform_map.get(sub.platform, sub.platform), sub.published_url)
        except Exception as e:  # noqa: BLE001
            audit(s, "analytics", "metrics_failed", "submission", sub.id, level="warning", error=str(e)[:200])
            continue
        views = post.get("view_count") or post.get("play_count")
        s.add(MetricSnapshot(submission_id=sub.id, source="crayo_tracker", views=views,
                             likes=post.get("like_count"), comments=post.get("comment_count"),
                             shares=post.get("share_count"), saves=post.get("save_count")))
        if views is not None:
            prev = sub.views or 0
            sub.views = views
            c = s.get(Campaign, sub.campaign_id)
            sub.earnings_estimated_usd = round(clip_gross(c, views), 2)   # ESTIMATE ONLY
            if views >= 100_000 and prev < 100_000:
                notify(s, "viral_clip", f"Submission #{sub.id} passed 100k public views: {sub.published_url}")
        n += 1
    return n
