"""Automated Quality Control. Every clip gets a 0-100 score and a full report.
90-100 ready · 80-89 acceptable · <80 regenerate/reject. Mandatory failures (campaign rules, rights,
duplicates, broken media) reject the clip regardless of score."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Campaign, Clip, ComplianceRule, SourceAsset
from .production import fingerprint_distance, probe, run

READY, ACCEPTABLE = 90, 80
DEFAULT_MIN_S, DEFAULT_MAX_S = 7.0, 90.0
DUP_THRESHOLD = 0.12   # fraction of differing fingerprint bits below which two clips are "the same"


def audio_levels(path: str) -> tuple[float, float]:
    err = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "volumedetect", "-f", "null", "-"]).stderr
    mean = re.search(r"mean_volume:\s*(-?[\d.]+)", err)
    peak = re.search(r"max_volume:\s*(-?[\d.]+)", err)
    return (float(mean.group(1)) if mean else -99.0, float(peak.group(1)) if peak else -99.0)


def build_caption(clip: Clip, campaign: Campaign | None, base: str = "") -> str:
    """Post caption: hook + required hashtags/tags/disclosures (mandatory items always included)."""
    parts = [base or clip.hook]
    if campaign:
        tags = [t if t.startswith(("#", "@")) else f"#{t}" for t in (campaign.required_hashtags or [])]
        mentions = [t if t.startswith("@") else f"@{t}" for t in (campaign.required_tags or [])]
        parts += [" ".join(mentions)] if mentions else []
        parts += [" ".join(tags)] if tags else []
        parts += list(campaign.required_disclosures or [])
    return "\n".join(p for p in parts if p).strip()


# Per-platform limits. Mandatory items (campaign hashtags, mentions, disclosures) are never cut;
# only the hook text and optional hashtags are shortened.
PLATFORM_LIMITS = {"tiktok": (2200, 5), "instagram": (2200, 10), "youtube": (5000, 3), "x": (280, 2),
                   "facebook": (2200, 5)}


def platform_caption(platform: str, clip: Clip, campaign: Campaign | None, extra_tags: list[str] | None = None) -> str:
    limit, max_optional = PLATFORM_LIMITS.get(platform, (2200, 5))
    mandatory = []
    if campaign:
        mandatory += [t if t.startswith("@") else f"@{t}" for t in (campaign.required_tags or [])]
        mandatory += [t if t.startswith("#") else f"#{t}" for t in (campaign.required_hashtags or [])]
        mandatory += list(campaign.required_disclosures or [])
    have = {m.lower() for m in mandatory}
    optional = [t for t in (extra_tags or []) if t.lower() not in have][:max_optional]
    if platform == "youtube" and "#shorts" not in have:
        optional = ["#shorts"] + optional[: max(0, max_optional - 1)]
    tail = " ".join(mandatory + optional)
    room = limit - len(tail) - 1
    hook = (clip.hook or "").strip()
    if len(hook) > room:
        hook = hook[: max(0, room - 1)].rsplit(" ", 1)[0] + "…" if room > 10 else ""
    return (hook + ("\n" if hook and tail else "") + tail).strip()


def youtube_title(clip: Clip) -> str:
    t = (clip.hook or "Clip").strip()
    return (t if len(t) <= 90 else t[:89].rsplit(" ", 1)[0] + "…") + " #shorts"


def run_qc(s: Session, clip: Clip, *, account_id: int | None = None) -> dict[str, Any]:
    campaign = s.get(Campaign, clip.campaign_id) if clip.campaign_id else None
    src = s.get(SourceAsset, clip.source_id)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, penalty: float, detail: str = "", mandatory: bool = False) -> None:
        checks.append({"check": name, "ok": bool(ok), "penalty": 0 if ok else penalty, "mandatory": mandatory,
                       "detail": detail})

    if not clip.path:
        add("render", False, 100, "no rendered file", True)
        return _finish(clip, checks)
    info = probe(clip.path)
    v = next((x for x in info["streams"] if x["codec_type"] == "video"), {})
    a = next((x for x in info["streams"] if x["codec_type"] == "audio"), None)
    dur = float(info["format"].get("duration", 0))
    w, h = int(v.get("width", 0)), int(v.get("height", 0))

    # --- technical
    add("resolution_1080x1920", (w, h) == (1080, 1920), 15, f"{w}x{h}")
    add("aspect_9x16", h > 0 and abs(w / h - 9 / 16) < 0.01, 30, f"{w}:{h}", mandatory=True)
    lo = (campaign.min_duration_s if campaign and campaign.min_duration_s else DEFAULT_MIN_S)
    hi = (campaign.max_duration_s if campaign and campaign.max_duration_s else DEFAULT_MAX_S)
    add("duration", lo <= dur <= hi, 40, f"{dur:.1f}s (allowed {lo:.0f}-{hi:.0f}s)",
        mandatory=bool(campaign and (campaign.min_duration_s or campaign.max_duration_s)))
    add("audio_present", a is not None, 50, "", mandatory=True)
    if a is not None:
        mean, peak = audio_levels(clip.path)
        add("audio_not_silent", mean > -35, 30, f"mean {mean} dB", mandatory=True)
        add("audio_not_clipping", peak <= -0.5, 5, f"peak {peak} dB")
        add("audio_loudness", -22 <= mean <= -8, 5, f"mean {mean} dB")

    # --- captions (built from word timestamps; check coverage, sync and placement)
    words = (clip.qc_report or {}).get("words") or []
    ass_path = clip.path.rsplit(".", 1)[0] + ".ass"
    try:
        ass = open(ass_path, encoding="utf-8").read()
    except OSError:
        ass = ""
    events = re.findall(r"Dialogue: 0,([\d:.]+),([\d:.]+),Cap", ass)
    add("captions_present", bool(events), 20, f"{len(events)} caption events")
    if words and events:
        low_conf = sum(1 for x in words if x.get("p", 1) < 0.5) / len(words)
        add("caption_accuracy", low_conf < 0.15, 10, f"{low_conf:.0%} low-confidence words")
        to_s = lambda t: sum(float(x) * 60 ** i for i, x in enumerate(reversed(t.split(":"))))  # noqa: E731
        starts = [to_s(e[0]) for e in events]
        add("caption_sync", starts == sorted(starts) and starts[-1] <= dur + 0.5 and starts[0] < 3, 15,
            f"first {starts[0]:.2f}s last {starts[-1]:.2f}s")
    mv = re.search(r"Style: Cap,(?:[^,]*,){20}(\d+)", ass)
    if mv:
        y = 1 - int(mv.group(1)) / 1920
        add("caption_safe_zone", 0.55 <= y <= 0.8, 5, f"baseline at {y:.0%} of height")

    # --- content integrity
    first = (words[0]["w"] if words else "").lower().strip(",.")
    last = words[-1]["w"] if words else ""
    add("starts_clean", first not in {"and", "but", "so", "or", "because", "which"}, 5, f"starts with '{first}'")
    add("ends_on_sentence", bool(re.search(r"[.!?…]$", last)), 8, f"ends with '{last}'")
    add("moment_quality", (clip.moment_score or 0) >= 35, 10, f"moment score {clip.moment_score}")

    # --- rights & campaign compliance (mandatory)
    add("usage_rights_recorded", bool(src and src.rights), 100, (src.rights if src else "")[:120], mandatory=True)
    if campaign:
        add("source_belongs_to_campaign", src is not None and src.campaign_id == campaign.id, 100,
            "source must be registered under this campaign", mandatory=True)
        add("campaign_active", campaign.status == "active", 100, campaign.status, mandatory=True)
        caption = clip.caption or build_caption(clip, campaign)
        for tag in campaign.required_hashtags or []:
            t = tag if tag.startswith("#") else f"#{tag}"
            add(f"hashtag:{t}", t.lower() in caption.lower(), 100, "", mandatory=True)
        for d in campaign.required_disclosures or []:
            add(f"disclosure:{d[:30]}", d.lower() in caption.lower(), 100, "", mandatory=True)
        if not campaign.brief_complete:
            add("brief_fully_read", False, 0, "Some campaign rules are only visible after login — "
                "confirm them before publishing", mandatory=False)

    # --- duplicates (same campaign) — mandatory
    if clip.content_hash:
        # only earlier clips count: the first rendition of a moment wins, later look-alikes are rejected
        q = select(Clip).where(Clip.id < clip.id, Clip.content_hash != "", Clip.status.not_in(["failed", "qc_rejected"]))
        if campaign:
            q = q.where(Clip.campaign_id == campaign.id)
        dup = next((o for o in s.scalars(q) if fingerprint_distance(o.content_hash, clip.content_hash) < DUP_THRESHOLD), None)
        add("not_duplicate", dup is None, 100, f"looks like clip {dup.id}" if dup else "", mandatory=True)
        overlap = s.scalar(select(Clip).where(Clip.id < clip.id, Clip.source_id == clip.source_id,
                                              Clip.variant == clip.variant, Clip.start_s < clip.end_s - 3,
                                              Clip.end_s > clip.start_s + 3, Clip.status.not_in(["failed", "qc_rejected"])))
        add("no_overlap_same_variant", overlap is None, 20, f"overlaps clip {overlap.id}" if overlap else "")

    # --- learned rules from past rejections
    for r in s.scalars(select(ComplianceRule)):
        if r.campaign_id not in (None, clip.campaign_id) or (campaign and r.marketplace not in ("*", campaign.marketplace)):
            continue
        chk = r.check or {}
        if chk.get("type") == "max_duration":
            add(f"learned:{r.id}", dur <= float(chk["value"]), 100, r.rule, mandatory=True)
        elif chk.get("type") == "forbidden_phrase":
            add(f"learned:{r.id}", chk["value"].lower() not in (clip.text or "").lower(), 100, r.rule, mandatory=True)
        elif chk.get("type") == "required_phrase_in_caption":
            add(f"learned:{r.id}", chk["value"].lower() in (clip.caption or "").lower(), 100, r.rule, mandatory=True)

    return _finish(clip, checks)


def _finish(clip: Clip, checks: list[dict[str, Any]]) -> dict[str, Any]:
    score = max(0.0, 100 - sum(c["penalty"] for c in checks))
    mandatory_fail = [c for c in checks if c["mandatory"] and not c["ok"]]
    if mandatory_fail:
        decision = "reject"
    elif score >= READY:
        decision = "ready"
    elif score >= ACCEPTABLE:
        decision = "acceptable"
    else:
        decision = "regenerate"
    report = {"score": round(score, 1), "decision": decision, "checks": checks,
              "mandatory_failures": [c["check"] for c in mandatory_fail],
              "words": (clip.qc_report or {}).get("words", [])}
    clip.qc_score, clip.qc_report = report["score"], report
    clip.status = "qc_approved" if decision in ("ready", "acceptable") else "qc_rejected"
    return report
