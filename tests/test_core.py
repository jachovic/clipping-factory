import datetime as dt
import json

import pytest

from factory import campaigns as camp
from factory.integrations import seed
from factory.marketplaces.base import CampaignData, parse_money
from factory.marketplaces.content_rewards import parse_detail, parse_discover
from factory.marketplaces.vyro import parse_board
from factory.models import Campaign, Clip, Job, LedgerEntry, SocialAccount, SourceAsset, Submission, now
from factory.ops import (RetryLater, SpendLimitExceeded, check_spend, claim_next_job, enqueue, record_cost,
                         recover_stale_jobs, redact, run_job, set_flag)


# ------------------------------------------------------------------ parsers
def _rsc_page(obj) -> str:
    payload = json.dumps(json.dumps(obj, separators=(",", ":")))[1:-1]  # JSON string-escaped, as Next.js embeds it
    return f'<script>self.__next_f.push([1,"{payload}"])</script>'


def test_parse_money():
    assert parse_money("$$1.50") == (1.5, 1.5)
    assert parse_money("⁦$0.50–$1⁩") == (0.5, 1.0)
    assert parse_money("$2,000") == (2000.0, 2000.0)
    assert parse_money(None) == (None, None)


def test_content_rewards_discover_and_detail():
    camp_obj = {"campaign": {"id": "abc", "title": "$$5,000 Test", "brand": "B", "category": "Gaming",
                             "ratePer1kLabel": "$$1.50", "type": "cpm", "maxPayoutCents": 50000,
                             "budgetTotalRaw": 5000, "availableBudgetRaw": 4000, "platforms": ["tiktok", "youtube"],
                             "requiresApplication": False, "opensAt": None, "creatorCountRaw": 3,
                             "submissionCountRaw": 9, "description": "Clip it"}, "metrics": {"totalViews": 1}}
    found = parse_discover(_rsc_page({"queries": [{"state": {"data": [camp_obj]}}]}))
    assert len(found) == 1
    c = found[0]
    assert c.name == "$5,000 Test" and c.cpm_usd == 1.5 and c.max_payout_per_video_usd == 500
    detail = {"banner": "x", "id": "abc", "description": "Use stream 1 only", "status": "active",
              "configuration": {"payoutModel": [{"platform": "tiktok", "rate": 100, "minPayout": 500, "maxPayout": 30000},
                                                {"platform": "youtube", "rate": 0, "minPayout": 0, "maxPayout": 0}],
                                "requirement": {"preApproval": True}, "budget": 500000},
              "briefCounts": {"contentRules": 2, "creatorRequirements": 0, "references": 1}}
    c = parse_detail(_rsc_page(detail), c)
    assert c.platforms == ["tiktok"]            # rate-0 platform dropped
    assert c.cpm_usd == 1.0 and c.min_payout_usd == 5.0 and c.max_payout_per_video_usd == 300.0
    assert c.requires_application is True and c.brief_complete is False


def test_vyro_board():
    md = ("[### Brand A\n\n9h ago•\n\nPer View\n\nCampaign One\n\nCampaign One\n\n$1,400\n\nPer 1M Views]"
          "(https://vyro.com/campaigns/one-abc)\n\n[### Brand B\n\n4d ago•\n\nPer View\n\nLocked for new posts\n\n"
          "Two\n\nTwo\n\n$2,000\n\nPer 1M Views](https://vyro.com/campaigns/two-def)")
    out = parse_board(md)
    assert [(c.name, c.cpm_usd, c.status) for c in out] == [("Campaign One", 1.4, "active"), ("Two", 2.0, "locked")]


# --------------------------------------------------------- campaign engine
def _cd(**kw):
    base = dict(marketplace="content_rewards", external_id="e1", name="Clip campaign", url="u", payment_model="cpm",
                cpm_usd=2.0, budget_total_usd=10000, budget_available_usd=9000, platforms=["tiktok", "youtube"],
                max_payout_per_video_usd=500, min_payout_usd=None)
    base.update(kw)
    return CampaignData(**base)


def test_upsert_change_detection_and_scoring(s):
    seed(s)
    c, ev = camp.upsert_campaign(s, _cd())
    assert ev == ["new"]
    c2, ev = camp.upsert_campaign(s, _cd(cpm_usd=1.0))
    assert c2.id == c.id and ev == ["changed"]
    camp.rescore_all(s)
    assert 0 < c.score <= 100 and c.priority in ("HIGH", "MEDIUM", "LOW")
    _, ev = camp.upsert_campaign(s, _cd(status="closed"))
    assert ev == ["closed"]
    camp.rescore_all(s)
    assert c.score == 0 and c.priority == "LOW"


def test_min_payout_threshold_kills_expected_value(s):
    seed(s)
    hi, _ = camp.upsert_campaign(s, _cd(external_id="a"))
    lo, _ = camp.upsert_campaign(s, _cd(external_id="b", min_payout_usd=20))  # needs 10k views/clip
    camp.rescore_all(s)
    assert lo.profitability["expected"]["gross_usd"] == 0
    assert lo.profitability["min_views_to_earn"] == 10000
    assert hi.score > lo.score


def test_profit_capped_by_budget_and_video_cap(s):
    c, _ = camp.upsert_campaign(s, _cd(budget_available_usd=10, max_payout_per_video_usd=1))
    p = camp.profitability(s, c, clips=6)
    assert p["high"]["gross_usd"] <= 10
    assert camp.clip_gross(c, 1_000_000) == 1


def test_language_detection():
    assert camp.detect_language("FR Yomi Denzel", "") == "fr"
    assert camp.detect_language("Clip campaign", "You must clip the stream and post with the hashtag for this brand") == "en"


# -------------------------------------------------------------- job queue
def test_job_dedupe_claim_retry_and_failure(s):
    assert enqueue(s, "x", dedupe_key="k1") is not None
    assert enqueue(s, "x", dedupe_key="k1") is None
    s.commit()
    job = claim_next_job(s)
    assert job and job.status == "running"
    calls = {"n": 0}

    def boom(sess, j):
        calls["n"] += 1
        raise ValueError("nope")
    run_job(job.id, {"x": boom})
    s.expire_all()
    j = s.get(Job, job.id)
    assert j.status == "queued" and j.attempts == 1 and "nope" in j.last_error
    j.run_at, j.attempts = now(), j.max_attempts - 1
    s.commit()
    job = claim_next_job(s)
    run_job(job.id, {"x": boom})
    s.expire_all()
    assert s.get(Job, job.id).status == "failed"


def test_retry_later_does_not_burn_attempts(s):
    enqueue(s, "y")
    s.commit()
    job = claim_next_job(s)

    def later(sess, j):
        raise RetryLater("wait", dt.timedelta(minutes=10))
    run_job(job.id, {"y": later})
    s.expire_all()
    j = s.get(Job, job.id)
    assert j.status == "queued" and j.attempts == 0 and j.run_at > now()


def test_pause_blocks_new_work_and_stop_blocks_all(s):
    enqueue(s, "publish", {"submission_id": 1})
    enqueue(s, "metrics")
    set_flag(s, "run_state", "paused")
    s.commit()
    assert claim_next_job(s).kind == "metrics"
    assert claim_next_job(s) is None          # publish withheld while paused
    set_flag(s, "run_state", "stopped")
    enqueue(s, "metrics")
    s.commit()
    assert claim_next_job(s) is None


def test_stale_job_recovery(s):
    j = enqueue(s, "z")
    j.status, j.locked_at = "running", now() - dt.timedelta(hours=2)
    s.commit()
    assert recover_stale_jobs(s) == 1


# ------------------------------------------------------------ money & safety
def test_spend_limits(s, monkeypatch):
    import dataclasses
    from factory import ops
    monkeypatch.setattr(ops, "settings", dataclasses.replace(ops.settings, daily_spend_limit_usd=1.0))
    record_cost(s, "crayo_credits", 0.8)
    s.flush()
    check_spend(s, 0.1, "ok")
    with pytest.raises(SpendLimitExceeded):
        check_spend(s, 0.5, "too much")


def test_redact():
    assert "abc123" not in redact("Authorization: Bearer abc123 and crayo_sk_live_999")
    assert "crayo_sk_***" in redact("key crayo_sk_live_999")


def _published_setup(s):
    seed(s)
    c, _ = camp.upsert_campaign(s, _cd(marketplace="clipster", external_id="cl1"))
    c.brief_complete = True
    src = SourceAsset(campaign_id=c.id, path="/tmp/x.mp4", rights="campaign brief u, provided for clipping")
    s.add(src)
    s.flush()
    clip = Clip(campaign_id=c.id, source_id=src.id, status="qc_approved", path="/tmp/x.mp4",
                qc_report={"decision": "ready"})
    s.add(clip)
    s.flush()
    return c, clip


def test_plan_submissions_no_duplicates(s):
    from factory.pipeline import plan_submissions
    c, clip = _published_setup(s)
    first = plan_submissions(s, clip)
    assert first, "should plan at least one submission"
    assert len({x.platform for x in first}) == len(first)   # one per platform
    assert plan_submissions(s, clip) == []                   # idempotent


def test_published_url_dedupe_and_deadline(s):
    from factory.pipeline import mark_published, plan_submissions
    c, clip = _published_setup(s)
    subs = plan_submissions(s, clip)
    mark_published(s, subs[0], "https://www.tiktok.com/@a/video/1")
    assert subs[0].status == "awaiting_submission"
    assert subs[0].submission_deadline_at is not None        # Clipster: 24h window
    if len(subs) > 1:
        with pytest.raises(ValueError):
            mark_published(s, subs[1], "https://www.tiktok.com/@a/video/1")


def test_rejection_creates_rule(s):
    from factory.models import ComplianceRule
    from factory.pipeline import plan_submissions, record_rejection
    c, clip = _published_setup(s)
    sub = plan_submissions(s, clip)[0]
    record_rejection(s, sub, "duration", "Clips must be under 45s")
    rule = s.query(ComplianceRule).one()
    assert rule.check == {"type": "max_duration", "value": 45.0}


# ------------------------------------------------------------------ telegram
def test_telegram_only_owner(s):
    from factory.telegram_bot import handle_text
    assert handle_text("/status", "999") is None
    out = handle_text("/status", "4242")
    assert "run_state" in out
    assert "Paused" in handle_text("/pause", "4242")
    assert "Resumed" in handle_text("/resume", "4242")


def test_paid_is_only_cash(s):
    from factory.manager import money
    from factory.telegram_bot import handle_text
    handle_text("/paid 12.5 content_rewards first payout", "4242")
    s.expire_all()
    m = money(s)
    assert m["paid"] == 12.5 and m["estimated"] == 0


# ----------------------------------------------------------------- dashboard
def test_dashboard_pages_render(s):
    from fastapi.testclient import TestClient
    from factory.dashboard.app import SECTIONS, app
    seed(s)
    camp.upsert_campaign(s, _cd())
    camp.rescore_all(s)
    s.commit()
    client = TestClient(app)
    for key, _ in SECTIONS:
        r = client.get(f"/s/{key}")
        assert r.status_code == 200, key
    assert client.get("/healthz").json()["ok"] is True
