"""Self-check ("factory doctor"): verifies everything the factory needs on this machine and reports in Polish.
Never prints secret values - only whether they are set."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass

import httpx

from .config import secret_present, settings


@dataclass
class Check:
    name: str
    ok: bool | None          # True ok · False problem · None optional/info
    detail: str
    fix: str = ""


HOSTS = {
    "contentrewards.com": "wyszukiwanie kampanii (Content Rewards)",
    "vyro.com": "wyszukiwanie kampanii (Vyro)",
    "api.crayo.ai": "Crayo API",
    "api.telegram.org": "bot Telegram",
    "huggingface.co": "pobranie modelu rozpoznawania mowy",
}


def _ffmpeg_checks() -> list[Check]:
    out = []
    ff = shutil.which("ffmpeg")
    out.append(Check("FFmpeg", bool(ff), ff or "nie znaleziono",
                     "Uruchom instalator ponownie albo: winget install Gyan.FFmpeg" if not ff else ""))
    if ff:
        filters = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        has = " ass " in filters or " subtitles " in filters
        out.append(Check("FFmpeg: napisy (libass)", has, "jest" if has else "brak filtra ass/subtitles",
                         "Zainstaluj pełną wersję FFmpeg (Gyan.FFmpeg)" if not has else ""))
    return out


def run_checks(download_model: bool = False, network: bool = True) -> list[Check]:
    c: list[Check] = []
    v = sys.version_info
    c.append(Check("Python", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro}", "Potrzebny Python 3.11+" if v < (3, 11) else ""))
    c += _ffmpeg_checks()
    try:
        from .models import init_db
        init_db()
        c.append(Check("Baza danych", True, settings.db_url.split("///")[-1] if "sqlite" in settings.db_url else "PostgreSQL"))
    except Exception as e:  # noqa: BLE001
        c.append(Check("Baza danych", False, str(e)[:120], "Sprawdź uprawnienia do folderu data"))
    try:
        test = settings.data_dir / ".write_test"
        test.write_text("ok")
        test.unlink()
        free = shutil.disk_usage(settings.data_dir).free / 1024 ** 3
        c.append(Check("Miejsce na dysku", free > 5, f"{free:.0f} GB wolne", "Zwolnij miejsce (klipy zajmują ~20 MB)" if free <= 5 else ""))
    except OSError as e:
        c.append(Check("Folder data", False, str(e)[:120], "Uruchom z folderu, do którego masz zapis"))
    # speech model
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    model_dir = settings.data_dir / "models"
    have_model = any(model_dir.glob(f"models--Systran--faster-whisper-{settings.whisper_model}/snapshots/*/model.bin"))
    if not have_model and download_model:
        try:
            from faster_whisper import WhisperModel
            WhisperModel(settings.whisper_model, device="cpu", compute_type="int8", download_root=str(model_dir))
            have_model = True
        except Exception as e:  # noqa: BLE001
            c.append(Check("Model mowy (Whisper)", False, f"pobieranie nieudane: {type(e).__name__}",
                           "Sprawdź internet i uruchom: factory doctor --download-model"))
    if have_model or not download_model:
        c.append(Check("Model mowy (Whisper)", True if have_model else None,
                       "pobrany" if have_model else "pobierze się przy pierwszym klipie (~140 MB)"))
    try:
        import cv2  # noqa: F401
        c.append(Check("Kadrowanie na twarz (OpenCV)", True, "jest"))
    except ImportError:
        c.append(Check("Kadrowanie na twarz (OpenCV)", None, "brak — klipy z rozmytym tłem", "pip install opencv-python-headless<5"))
    if network:
        for host, why in HOSTS.items():
            try:
                httpx.head(f"https://{host}/", timeout=10, follow_redirects=False)
                c.append(Check(f"Internet: {host}", True, why))
            except httpx.HTTPError as e:
                c.append(Check(f"Internet: {host}", False, f"{why}: {type(e).__name__}",
                               "Sprawdź połączenie / zaporę / antywirusa"))
    # keys (presence only)
    c.append(Check("Klucz Crayo", True if secret_present("CRAYO_API_KEY") else None,
                   "ustawiony" if secret_present("CRAYO_API_KEY") else "brak — klipy robi silnik lokalny",
                   "" if secret_present("CRAYO_API_KEY") else "Konfiguracja → Crayo API key"))
    tg = secret_present("TELEGRAM_BOT_TOKEN")
    c.append(Check("Bot Telegram", True if tg and settings.telegram_owner_id else None,
                   "gotowy" if tg and settings.telegram_owner_id else ("brak Twojego ID" if tg else "brak tokenu"),
                   "" if tg and settings.telegram_owner_id else "Konfiguracja → Telegram"))
    try:
        from .models import SocialAccount
        from .ops import session
        with session() as s:
            named = s.query(SocialAccount).filter(SocialAccount.username != "").count()
        c.append(Check("Konta społecznościowe", True if named else None, f"{named}/11 z nazwą",
                       "" if named else "Konfiguracja → Konta"))
    except Exception:  # noqa: BLE001
        pass
    # dashboard port
    with socket.socket() as sock:
        sock.settimeout(1)
        running = sock.connect_ex(("127.0.0.1", 8080)) == 0
    c.append(Check("Dashboard :8080", None, "działa" if running else "nie działa (uruchomi się z autostartem)"))
    return c


def render_text(checks: list[Check]) -> str:
    icon = {True: "OK  ", False: "BŁĄD", None: "INFO"}
    lines = [f"[{icon[ch.ok]}] {ch.name}: {ch.detail}" + (f"\n        → {ch.fix}" if ch.fix else "") for ch in checks]
    bad = [ch for ch in checks if ch.ok is False]
    lines.append("")
    lines.append("WSZYSTKO GOTOWE do startu." if not bad else f"Do naprawy: {len(bad)} — zobacz BŁĄD powyżej.")
    return "\n".join(lines)
