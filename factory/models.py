"""Database schema. The database is the single source of truth for the factory."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------- integrations
INTEGRATION_STATUSES = ["not_connected", "in_progress", "awaiting_user", "connected",
                        "partial", "error", "disabled"]


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(64))           # e.g. crayo, content_rewards, tiktok
    label: Mapped[str] = mapped_column(String(128))            # e.g. "TikTok #1"
    kind: Mapped[str] = mapped_column(String(32))              # marketplace|social|tool|notify
    status: Mapped[str] = mapped_column(String(32), default="not_connected")
    auth_method: Mapped[str] = mapped_column(String(128), default="")
    account_identifier: Mapped[str] = mapped_column(String(256), default="")
    permissions: Mapped[list[Any]] = mapped_column(default=list)
    operations: Mapped[dict[str, Any]] = mapped_column(default=dict)  # op -> auto|manual|unsupported
    last_test_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    manual_action: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("service", "label"),)


# ------------------------------------------------------------------- campaigns
class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(Text, default="")
    brand: Mapped[str] = mapped_column(String(256), default="")
    category: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    payment_model: Mapped[str] = mapped_column(String(32), default="cpm")  # cpm|pool|fixed|unknown
    cpm_usd: Mapped[float | None] = mapped_column(Float, nullable=True)     # lower bound if a range
    cpm_max_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    fixed_reward_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_payout_per_video_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_payout_per_account_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_payout_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_total_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_available_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    platforms: Mapped[list[Any]] = mapped_column(default=list)
    language: Mapped[str] = mapped_column(String(32), default="")
    geography: Mapped[list[Any]] = mapped_column(default=list)
    min_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    required_hashtags: Mapped[list[Any]] = mapped_column(default=list)
    required_tags: Mapped[list[Any]] = mapped_column(default=list)
    required_disclosures: Mapped[list[Any]] = mapped_column(default=list)
    source_links: Mapped[list[Any]] = mapped_column(default=list)
    requires_application: Mapped[bool] = mapped_column(Boolean, default=False)
    starts_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    submission_window_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    duplicate_rules: Mapped[str] = mapped_column(Text, default="")
    payout_conditions: Mapped[str] = mapped_column(Text, default="")
    eligibility: Mapped[str] = mapped_column(Text, default="")
    creator_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submission_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|locked|closed|upcoming
    brief_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    joined: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="")
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(default=dict)
    profitability: Mapped[dict[str, Any]] = mapped_column(default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("marketplace", "external_id"),)


class CampaignEvent(Base):
    """Detected changes: new, closed, rate change, budget change, rule change."""
    __tablename__ = "campaign_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------------------------ production
class SourceAsset(Base):
    __tablename__ = "source_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    path: Mapped[str] = mapped_column(Text)
    origin_url: Mapped[str] = mapped_column(Text, default="")
    rights: Mapped[str] = mapped_column(Text)     # REQUIRED: why we may use this file
    sha256: Mapped[str] = mapped_column(String(64), default="")
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcript: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


CLIP_STATUSES = ["planned", "in_production", "rendered", "qc_approved", "qc_rejected", "ready_to_publish",
                 "published", "failed"]


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source_assets.id"))
    engine: Mapped[str] = mapped_column(String(16), default="local")  # local|crayo
    start_s: Mapped[float] = mapped_column(Float, default=0)
    end_s: Mapped[float] = mapped_column(Float, default=0)
    moment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    moment_breakdown: Mapped[dict[str, Any]] = mapped_column(default=dict)
    hook: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    variant: Mapped[str] = mapped_column(String(64), default="v1")
    path: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="planned")
    qc_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    qc_report: Mapped[dict[str, Any]] = mapped_column(default=dict)
    caption: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    external_ref: Mapped[str] = mapped_column(String(128), default="")  # e.g. crayo project id
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


# -------------------------------------------------------------------- accounts
class SocialAccount(Base):
    __tablename__ = "social_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))  # tiktok|instagram|youtube|x|facebook
    label: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(128), default="")
    platform_account_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="registered")  # registered|active|paused|banned
    auth_status: Mapped[str] = mapped_column(String(32), default="not_connected")
    publish_method: Mapped[str] = mapped_column(String(32), default="manual")  # api|manual
    account_type: Mapped[str] = mapped_column(String(32), default="")  # e.g. business|creator|personal
    language: Mapped[str] = mapped_column(String(16), default="en")
    niche: Mapped[str] = mapped_column(String(64), default="")
    geography: Mapped[str] = mapped_column(String(64), default="")
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_views: Mapped[float | None] = mapped_column(Float, nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    health: Mapped[str] = mapped_column(String(32), default="unknown")
    marketplaces: Mapped[list[Any]] = mapped_column(default=list)  # marketplaces this account is linked on
    daily_post_cap: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("platform", "label"),)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    __table_args__ = (UniqueConstraint("campaign_id", "account_id"),)


# ------------------------------------------------------- publishing/submissions
SUBMISSION_STATUSES = ["planned", "in_production", "qc_approved", "ready_to_publish", "published",
                       "awaiting_submission", "submitted", "under_review", "approved", "rejected",
                       "earning", "pending_payout", "paid"]


class Submission(Base):
    """One clip, on one account, for one campaign — from plan to payout."""
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    marketplace: Mapped[str] = mapped_column(String(64))
    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id"))
    platform: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="ready_to_publish")
    caption: Mapped[str] = mapped_column(Text, default="")
    scheduled_for: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    published_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    publish_confirmation: Mapped[str] = mapped_column(Text, default="")
    submission_deadline_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    submission_confirmation: Mapped[str] = mapped_column(Text, default="")
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    rejection_category: Mapped[str] = mapped_column(String(64), default="")
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligible_views: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ONLY from marketplace
    earnings_estimated_usd: Mapped[float] = mapped_column(Float, default=0)
    earnings_pending_usd: Mapped[float] = mapped_column(Float, default=0)
    earnings_approved_usd: Mapped[float] = mapped_column(Float, default=0)
    earnings_paid_usd: Mapped[float] = mapped_column(Float, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __table_args__ = (UniqueConstraint("campaign_id", "clip_id", "account_id"),)


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    source: Mapped[str] = mapped_column(String(32))  # platform_api|crayo_tracker|marketplace|manual
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligible_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ------------------------------------------------------------------- money
MONEY_STATUSES = ["estimated", "pending", "approved", "withdrawable", "paid"]


class LedgerEntry(Base):
    """Every cost and every revenue figure, with an explicit confidence status.
    Only status == 'paid' revenue is money actually received."""
    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    kind: Mapped[str] = mapped_column(String(16))      # cost|revenue
    category: Mapped[str] = mapped_column(String(64))  # crayo_credits|subscription|ai_api|hosting|payout...
    amount_usd: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="estimated")
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id"), nullable=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- operations
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|cancelled
    dedupe_key: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    run_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    level: Mapped[str] = mapped_column(String(16), default="info")
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Approval(Base):
    """Actions that need the owner's explicit OK (spending, legal terms, unclear rights...)."""
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class ComplianceRule(Base):
    """Rules learned from rejections; enforced by QC on future clips."""
    __tablename__ = "compliance_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(64), default="*")
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    rule: Mapped[str] = mapped_column(Text)
    check: Mapped[dict[str, Any]] = mapped_column(default=dict)  # machine-checkable form, if any
    source_submission_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    hypothesis: Mapped[str] = mapped_column(Text)
    variable: Mapped[str] = mapped_column(String(64))           # e.g. clip_length, hook_style
    arms: Mapped[dict[str, Any]] = mapped_column(default=dict)  # arm -> description
    min_sample_per_arm: Mapped[int] = mapped_column(Integer, default=10)
    results: Mapped[dict[str, Any]] = mapped_column(default=dict)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="running")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# ------------------------------------------------------------------ engine
_url = settings.db_url
engine = create_engine(_url, future=True,
                       connect_args={"check_same_thread": False, "timeout": 30} if _url.startswith("sqlite") else {})

if _url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)
