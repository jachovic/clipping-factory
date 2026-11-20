"""Operational primitives: audit log, run-state flags, DB-backed job queue,
spending guard and approval requests."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .models import Approval, AuditLog, Job, LedgerEntry, SessionLocal, Setting, now

log = logging.getLogger("factory")

_SECRET_PAT = re.compile(r"(crayo_sk_|Bearer\s+|sk-ant-|bot\d+:)[A-Za-z0-9_\-:.]+")


def redact(text: str) -> str:
    """Strip anything that looks like a credential before it reaches logs or the DB."""
    return _SECRET_PAT.sub(lambda m: m.group(1) + "***", text or "")


@contextmanager
def session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def audit(s: Session, actor: str, action: str, entity: str = "", entity_id: Any = "",
          level: str = "info", **detail: Any) -> None:
    detail = json.loads(redact(json.dumps(detail, default=str)))
    s.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=str(entity_id),
                   level=level, detail=detail))
    getattr(log, level if level in ("info", "warning", "error") else "info")(
        "%s %s %s#%s %s", actor, action, entity, entity_id, detail)


# ------------------------------------------------------------ run-state flags
def get_flag(s: Session, key: str, default: str = "") -> str:
    row = s.get(Setting, key)
    return row.value if row else default


def set_flag(s: Session, key: str, value: str) -> None:
    row = s.get(Setting, key)
    if row:
        row.value = value
    else:
        s.add(Setting(key=key, value=value))


def run_state(s: Session) -> str:
    """running | paused (no NEW production/publishing) | stopped (emergency: nothing runs)."""
    return get_flag(s, "run_state", "running")


# ------------------------------------------------------------------ job queue
# Jobs that create new outward-facing work are blocked when paused.
NEW_WORK_KINDS = {"produce_clip", "crayo_autoclip", "publish", "submit"}


def enqueue(s: Session, kind: str, payload: dict | None = None, *, run_at: dt.datetime | None = None,
            dedupe_key: str | None = None, max_attempts: int = 3) -> Job | None:
    """Add a job. With dedupe_key, a second identical job is silently skipped."""
    if dedupe_key:
        existing = s.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing:
            return None
    job = Job(kind=kind, payload=payload or {}, run_at=run_at or now(), dedupe_key=dedupe_key,
              max_attempts=max_attempts)
    s.add(job)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        return None
    return job


def recover_stale_jobs(s: Session, older_than_min: int = 30) -> int:
    """Crash recovery: a job 'running' for too long belonged to a dead worker — requeue it."""
    cutoff = now() - dt.timedelta(minutes=older_than_min)
    res = s.execute(update(Job).where(Job.status == "running", Job.locked_at < cutoff)
                    .values(status="queued", locked_at=None))
    return res.rowcount or 0


def claim_next_job(s: Session) -> Job | None:
    state = run_state(s)
    if state == "stopped":
        return None
    q = select(Job).where(Job.status == "queued", Job.run_at <= now()).order_by(Job.run_at, Job.id)
    if state == "paused":
        q = q.where(Job.kind.not_in(NEW_WORK_KINDS))
    for job in s.scalars(q.limit(5)):
        # optimistic lock: only one worker wins the UPDATE
        res = s.execute(update(Job).where(Job.id == job.id, Job.status == "queued")
                        .values(status="running", locked_at=now(), attempts=Job.attempts + 1))
        if res.rowcount == 1:
            s.commit()
            s.refresh(job)
            return job
    return None


def run_job(job_id: int, handlers: dict[str, Callable[[Session, Job], dict | None]]) -> None:
    with session() as s:
        job = s.get(Job, job_id)
        handler = handlers.get(job.kind)
        try:
            if handler is None:
                raise RuntimeError(f"no handler for job kind {job.kind!r}")
            job.result = handler(s, job) or {}
            job.status, job.finished_at, job.last_error = "done", now(), ""
        except RetryLater as e:
            s.rollback()
            job = s.get(Job, job_id)
            job.status, job.run_at, job.attempts = "queued", now() + e.delay, max(0, job.attempts - 1)
            job.last_error = redact(str(e))
        except Exception as e:  # noqa: BLE001 - the worker must never die on one job
            s.rollback()
            job = s.get(Job, job_id)
            job.last_error = redact(f"{e}\n{traceback.format_exc()[-1500:]}")
            if job.attempts >= job.max_attempts:
                job.status, job.finished_at = "failed", now()
                audit(s, "worker", "job_failed", "job", job.id, level="error", kind=job.kind, error=str(e)[:500])
                from .notify import notify
                notify(s, "system_error", f"Job {job.kind} #{job.id} failed permanently: {redact(str(e))[:300]}")
            else:
                job.status = "queued"
                job.run_at = now() + dt.timedelta(minutes=2 ** job.attempts)  # exponential backoff
        job.locked_at = None


class RetryLater(Exception):
    def __init__(self, msg: str, delay: dt.timedelta = dt.timedelta(minutes=5)):
        super().__init__(msg)
        self.delay = delay


# ------------------------------------------------------------- spending guard
class SpendLimitExceeded(Exception):
    pass


def spent_since(s: Session, since: dt.datetime) -> float:
    return float(s.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0.0))
                          .where(LedgerEntry.kind == "cost", LedgerEntry.at >= since,
                                 LedgerEntry.category != "subscription")) or 0.0)


def check_spend(s: Session, amount_usd: float, what: str) -> None:
    """Raise unless a variable cost fits inside the configured daily and monthly limits."""
    t = now()
    day = spent_since(s, t.replace(hour=0, minute=0, second=0, microsecond=0))
    month = spent_since(s, t.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    if day + amount_usd > settings.daily_spend_limit_usd:
        raise SpendLimitExceeded(f"{what}: ${amount_usd:.2f} would exceed daily limit "
                                 f"(${day:.2f}/${settings.daily_spend_limit_usd:.2f})")
    if month + amount_usd > settings.monthly_spend_limit_usd:
        raise SpendLimitExceeded(f"{what}: ${amount_usd:.2f} would exceed monthly limit "
                                 f"(${month:.2f}/${settings.monthly_spend_limit_usd:.2f})")


def record_cost(s: Session, category: str, amount_usd: float, note: str = "", **refs: Any) -> None:
    s.add(LedgerEntry(kind="cost", category=category, amount_usd=amount_usd, status="paid", note=note, **refs))


# ------------------------------------------------------------------ approvals
def request_approval(s: Session, kind: str, summary: str, payload: dict | None = None) -> Approval:
    """Queue something for the owner. Identical pending requests are batched, not duplicated."""
    existing = s.scalar(select(Approval).where(Approval.kind == kind, Approval.summary == summary,
                                               Approval.status == "pending"))
    if existing:
        return existing
    a = Approval(kind=kind, summary=summary, payload=payload or {})
    s.add(a)
    s.flush()
    from .notify import notify
    notify(s, "approval_needed", f"Approval #{a.id} needed ({kind}): {summary}\n/approve {a.id} or /reject {a.id}")
    return a


def is_approved(s: Session, kind: str, summary: str) -> bool:
    return s.scalar(select(Approval).where(Approval.kind == kind, Approval.summary == summary,
                                           Approval.status == "approved")) is not None
