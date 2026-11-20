"""Outbound notifications (Telegram). Messages go ONLY to the configured owner chat.
If Telegram is not configured, notifications are kept in the audit log so nothing is lost."""
from __future__ import annotations

import datetime as dt
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import secret, settings
from .models import AuditLog, now

log = logging.getLogger("factory.notify")

# Kinds that are always sent. Others are throttled to avoid noise.
URGENT = {"submission_deadline", "system_error", "approval_needed", "publish_failed", "emergency"}
THROTTLE_MIN = {"new_campaign": 30, "campaign_change": 60, "production_failed": 15, "viral_clip": 60,
                "earnings": 60, "payout": 0, "rejected_clip": 0, "low_budget": 180}


def telegram_send(text: str, chat_id: str | None = None) -> bool:
    token = secret("TELEGRAM_BOT_TOKEN")
    chat = chat_id or settings.telegram_owner_id
    if not token or not chat:
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True},
                       timeout=15)
        return r.status_code == 200
    except httpx.HTTPError as e:
        log.warning("telegram send failed: %s", type(e).__name__)
        return False


def notify(s: Session, kind: str, text: str) -> bool:
    window = 0 if kind in URGENT else THROTTLE_MIN.get(kind, 30)
    if window:
        # suppress an identical message of the same kind sent within the window
        recent = s.scalars(select(AuditLog).where(
            AuditLog.action == f"notify:{kind}",
            AuditLog.at >= now() - dt.timedelta(minutes=window))).all()
        if any(r.detail.get("text") == text[:2000] for r in recent):
            return False
    sent = telegram_send(f"[{kind}] {text}")
    s.add(AuditLog(actor="notify", action=f"notify:{kind}", detail={"text": text[:2000], "sent": sent}))
    return sent
