"""Publishing engine. Only official APIs are used; anything else becomes a manual task with the file,
caption and a one-tap confirmation command. A post is never marked published without a URL."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import secret
from .models import SocialAccount


@dataclass
class PublishResult:
    ok: bool
    url: str = ""
    confirmation: str = ""
    manual_task: str = ""
    error: str = ""


class ManualPublisher:
    def publish(self, path: Path, caption: str, account: SocialAccount) -> PublishResult:
        return PublishResult(ok=False, manual_task=f"Post {path.name} on {account.platform}/{account.label}")


class YouTubePublisher:
    """YouTube Data API v3 resumable upload (videos.insert). Needs a refresh token for the channel obtained
    through Google OAuth with scope youtube.upload, stored as YOUTUBE_REFRESH_TOKEN_<LABEL> plus
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET. NOTE: videos uploaded from an unverified API project are
    locked private until the project passes Google's audit — status 'auto_untested' until verified."""

    def _access_token(self, account: SocialAccount) -> str:
        key = f"YOUTUBE_REFRESH_TOKEN_{account.label.upper().replace('-', '_')}"
        rt, cid, cs = secret(key), secret("GOOGLE_CLIENT_ID"), secret("GOOGLE_CLIENT_SECRET")
        if not (rt and cid and cs):
            raise RuntimeError(f"{key}/GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET not configured")
        r = httpx.post("https://oauth2.googleapis.com/token", timeout=30,
                       data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid, "client_secret": cs})
        r.raise_for_status()
        return r.json()["access_token"]

    def publish(self, path: Path, caption: str, account: SocialAccount) -> PublishResult:
        try:
            tok = self._access_token(account)
        except Exception as e:  # noqa: BLE001
            return PublishResult(ok=False, manual_task="YouTube API not authorised", error=str(e)[:200])
        title = (caption.splitlines()[0] if caption else path.stem)[:95]
        meta = {"snippet": {"title": title + (" #Shorts" if "#shorts" not in title.lower() else ""),
                            "description": caption, "categoryId": "24"},
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
        h = {"Authorization": f"Bearer {tok}"}
        init = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                          headers={**h, "Content-Type": "application/json; charset=UTF-8",
                                   "X-Upload-Content-Type": "video/mp4"}, content=json.dumps(meta), timeout=60)
        if init.status_code != 200:
            return PublishResult(ok=False, error=f"init HTTP {init.status_code}: {init.text[:200]}")
        up = httpx.put(init.headers["Location"], headers={**h, "Content-Type": "video/mp4"},
                       content=path.read_bytes(), timeout=900)
        if up.status_code not in (200, 201):
            return PublishResult(ok=False, error=f"upload HTTP {up.status_code}: {up.text[:200]}")
        vid = up.json()
        privacy = vid.get("status", {}).get("privacyStatus")
        if privacy != "public":
            return PublishResult(ok=False, error=f"uploaded {vid['id']} but privacy={privacy} (unverified API project?)")
        return PublishResult(ok=True, url=f"https://www.youtube.com/shorts/{vid['id']}", confirmation=vid["id"])


def get_publisher(account: SocialAccount):
    if account.publish_method == "api" and account.platform == "youtube":
        return YouTubePublisher()
    return ManualPublisher()
