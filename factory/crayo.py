"""Crayo API client — written against https://crayo.ai/docs/openapi.json (Crayo API 1.0.0, 68 endpoints).

Facts from the spec:
* Base https://api.crayo.ai, header `Authorization: Bearer crayo_sk_…`; errors are {success:false, code}.
* GET /v1/account -> plan, credits{export,voiceover,image,video,tracker}, storage, rate_limit.
* POST /v1/assets {url} imports from a public https URL (<=100 MB); bigger files use
  POST /v1/uploads -> PUT bytes to signed url (start within 5 min) -> POST /v1/uploads/{id}/complete (video <=1 GB).
* POST /v1/autoclip {asset_id (1 min–3 h), clip_count 2–20, clip_length 30|60|90, edit_level clips|captions|cropped|full,
  formats, prompt<=500, mode speech|energy, language}. "Credits are charged for the clips you asked for when the job
  starts, and stay spent if the job finds fewer." Each finished clip is a project.
* POST /v1/projects/{id}/export — "1 export credit per second of finished video", charged when queued.
* GET /v1/exports/{id} -> status queued|processing|completed|failed; completed carries asset.url.
* POST /v1/transcripts {asset_id <=30 min} — 1 voiceover credit per second; cached per asset.
* Webhooks: export.completed/failed, autoclip.completed/failed, tracker.refreshed …, signed `crayo-signature`.
* View Tracker raw: GET /v1/view-tracker/raw/{platform}/post?url= — 1 tracker credit.
The spec does not state which AutoClip credit type is consumed; we read balances before/after the first job
to learn it and record that in the DB (setting `crayo_autoclip_credit_type`).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .config import secret

API = "https://api.crayo.ai"


class CrayoError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"Crayo {status} {code}: {message}")
        self.status, self.code = status, code


class CrayoClient:
    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        key = api_key or secret("CRAYO_API_KEY")
        if not key:
            raise RuntimeError("CRAYO_API_KEY not configured")
        self._c = client or httpx.Client(base_url=API, timeout=60,
                                         headers={"Authorization": f"Bearer {key}"})

    def _req(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        for attempt in range(4):
            r = self._c.request(method, path, **kw)
            if r.status_code == 429 and attempt < 3:      # respect rate limits
                time.sleep(float(r.headers.get("retry-after", 2 ** attempt)))
                continue
            break
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or body.get("success") is False:
            err = body.get("error") or {}
            raise CrayoError(r.status_code, str(err.get("code") or body.get("code") or "http_error"),
                             str(err.get("message") or body.get("message") or r.text[:200]))
        return body

    # -- account
    def account(self) -> dict[str, Any]:
        return self._req("GET", "/v1/account")

    # -- assets
    def import_url(self, url: str, name: str | None = None) -> dict[str, Any]:
        return self._req("POST", "/v1/assets", json={"url": url, **({"name": name} if name else {})})["asset"]

    def upload_file(self, path: str | Path, content_type: str = "video/mp4") -> dict[str, Any]:
        p = Path(path)
        up = self._req("POST", "/v1/uploads", json={"filename": p.name[:100], "content_type": content_type,
                                                     "size_bytes": p.stat().st_size})["upload"]
        with p.open("rb") as fh:
            r = httpx.request(up.get("method", "PUT"), up["url"], content=fh,
                              headers={"content-type": up["headers"]["content-type"]}, timeout=600)
        r.raise_for_status()
        return self._req("POST", f"/v1/uploads/{up['id']}/complete")["asset"]

    def asset(self, asset_id: str) -> dict[str, Any]:
        return self._req("GET", f"/v1/assets/{asset_id}")["asset"]

    # -- production
    def transcript(self, asset_id: str) -> dict[str, Any]:
        return self._req("POST", "/v1/transcripts", json={"asset_id": asset_id})["transcript"]

    def autoclip(self, asset_id: str, clip_count: int = 2, clip_length: int = 60, edit_level: str = "full",
                 prompt: str | None = None, language: str | None = None, mode: str = "speech") -> dict[str, Any]:
        if not 2 <= clip_count <= 20:
            raise ValueError("clip_count must be 2..20 (API limit)")
        if clip_length not in (30, 60, 90):
            raise ValueError("clip_length must be 30, 60 or 90")
        body: dict[str, Any] = {"asset_id": asset_id, "clip_count": clip_count, "clip_length": clip_length,
                                "edit_level": edit_level, "mode": mode}
        if prompt:
            body["prompt"] = prompt[:500]
        if language:
            body["language"] = language
        return self._req("POST", "/v1/autoclip", json=body)["autoclip"]

    def autoclip_job(self, job_id: str) -> dict[str, Any]:
        return self._req("GET", f"/v1/autoclip/{job_id}")["autoclip"]

    def export(self, project_id: str) -> dict[str, Any]:
        return self._req("POST", f"/v1/projects/{project_id}/export")["export"]

    def export_status(self, export_id: str) -> dict[str, Any]:
        return self._req("GET", f"/v1/exports/{export_id}")

    def wait_export(self, export_id: str, timeout_s: int = 1800, poll_s: int = 10) -> dict[str, Any]:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            res = self.export_status(export_id)
            st = res["export"]["status"]
            if st == "completed":
                return res
            if st == "failed":
                raise CrayoError(200, "export_failed", str(res["export"].get("error", "")))
            time.sleep(poll_s)
        raise TimeoutError(f"export {export_id} not finished after {timeout_s}s")

    # -- analytics
    def post_stats(self, platform: str, url: str) -> dict[str, Any]:
        return self._req("GET", f"/v1/view-tracker/raw/{platform}/post", params={"url": url})["post"]

    # -- webhooks
    def create_webhook(self, url: str, events: list[str]) -> dict[str, Any]:
        return self._req("POST", "/v1/webhooks", json={"url": url, "events": events})["webhook"]


def verify_webhook(body: bytes, signature_header: str, secret_value: str) -> bool:
    """Crayo signs deliveries with the per-webhook secret (header `crayo-signature`). The exact signature
    scheme is not in the OpenAPI document; we accept a plain hex HMAC-SHA256 of the raw body or a
    `t=…,v1=…` style header. Verify against a real test delivery (POST /v1/webhooks/{id}/test) before trusting."""
    if not signature_header or not secret_value:
        return False
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    if "v1" in parts and "t" in parts:
        msg = f"{parts['t']}.".encode() + body
        expected = hmac.new(secret_value.encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, parts["v1"])
    expected = hmac.new(secret_value.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


# ------------------------------------------------------------- cost model
def usd_per_credit(kind: str) -> float:
    """$ value of one credit, derived from the operator's plan (set in .env). 0 = unknown -> credits-only guard."""
    try:
        return float(os.environ.get(f"CRAYO_USD_PER_{kind.upper()}_CREDIT", "0"))
    except ValueError:
        return 0.0


def estimate_autoclip(clip_count: int, clip_length: int) -> dict[str, Any]:
    export_seconds = clip_count * clip_length          # upper bound; exports bill actual seconds
    return {"export_credits": export_seconds, "autoclip_clips": clip_count,
            "usd": round(export_seconds * usd_per_credit("export"), 4),
            "note": "AutoClip job credit type unknown until first run; export = 1 credit/second."}
