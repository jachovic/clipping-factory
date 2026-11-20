"""AI Growth Manager: performance breakdowns, experiment evaluation and recommendations.
Rule: no conclusion from fewer than MIN_SAMPLE data points per group, and medians (not means) so a single
viral post cannot drive strategy."""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Campaign, Clip, Experiment, SocialAccount, Submission
from .ops import audit

MIN_SAMPLE = 8


def _bucket_duration(d: float) -> str:
    return "<20s" if d < 20 else "20-35s" if d < 35 else "35-50s" if d < 50 else "50s+"


def breakdowns(s: Session) -> dict[str, dict[str, Any]]:
    rows = s.execute(select(Submission, Clip, SocialAccount, Campaign)
                     .join(Clip, Clip.id == Submission.clip_id)
                     .join(SocialAccount, SocialAccount.id == Submission.account_id)
                     .join(Campaign, Campaign.id == Submission.campaign_id)
                     .where(Submission.views.is_not(None))).all()
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for sub, clip, acc, c in rows:
        v = float(sub.views or 0)
        groups["duration"][_bucket_duration(clip.end_s - clip.start_s)].append(v)
        groups["platform"][sub.platform].append(v)
        groups["account"][acc.label].append(v)
        groups["campaign"][f"{c.id}:{c.name[:30]}"].append(v)
        groups["engine"][clip.engine].append(v)
        groups["variant"][clip.variant].append(v)
        if sub.published_at:
            groups["hour_utc"][f"{sub.published_at.hour:02d}"].append(v)
        groups["hook_question"]["yes" if "?" in (clip.hook or "") else "no"].append(v)
    out: dict[str, dict[str, Any]] = {}
    for dim, g in groups.items():
        out[dim] = {k: {"n": len(v), "median_views": statistics.median(v), "enough_data": len(v) >= MIN_SAMPLE}
                    for k, v in sorted(g.items())}
    return out


def recommendations(b: dict[str, dict[str, Any]]) -> list[str]:
    recs = []
    for dim, g in b.items():
        valid = {k: v for k, v in g.items() if v["enough_data"]}
        if len(valid) < 2:
            continue
        best = max(valid, key=lambda k: valid[k]["median_views"])
        worst = min(valid, key=lambda k: valid[k]["median_views"])
        if valid[best]["median_views"] >= 1.5 * max(1, valid[worst]["median_views"]):
            recs.append(f"{dim}: '{best}' median {valid[best]['median_views']:.0f} views vs '{worst}' "
                        f"{valid[worst]['median_views']:.0f} (n={valid[best]['n']}/{valid[worst]['n']}) — shift toward '{best}'.")
    if not recs:
        recs.append(f"Not enough data yet (need >= {MIN_SAMPLE} posts per group). Keep the running experiments going.")
    return recs


def evaluate_experiments(s: Session, b: dict[str, dict[str, Any]]) -> None:
    for e in s.scalars(select(Experiment).where(Experiment.status == "running")):
        g = b.get(e.variable, {})
        arms = {k: g.get(k) for k in e.arms}
        e.results = arms
        if all(a and a["n"] >= e.min_sample_per_arm for a in arms.values()):
            best = max(arms, key=lambda k: arms[k]["median_views"])
            e.conclusion = f"'{best}' wins on median views ({arms[best]['median_views']:.0f})."
            e.next_action = f"Default {e.variable} to '{best}'; start a new experiment on another variable."
            e.status = "concluded"


def growth_review(s: Session) -> dict[str, Any]:
    b = breakdowns(s)
    evaluate_experiments(s, b)
    recs = recommendations(b)
    audit(s, "growth", "review", recommendations=recs)
    return {"recommendations": recs}


def seed_experiments(s: Session) -> None:
    if s.scalar(select(Experiment)) is None:
        s.add(Experiment(hypothesis="Clips of 20-35s get higher median views than 50s+ clips on TikTok/Shorts.",
                         variable="duration", arms={"20-35s": "short", "50s+": "long"}, min_sample_per_arm=10))
        s.add(Experiment(hypothesis="Opening hooks phrased as a question outperform statements.",
                         variable="hook_question", arms={"yes": "question hook", "no": "statement hook"},
                         min_sample_per_arm=10))
