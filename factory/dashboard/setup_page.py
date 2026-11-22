"""Setup page: the only place the owner needs to touch. Keys are written to .env (never echoed back),
take effect immediately, and every connection can be tested with one click."""
from __future__ import annotations

import html
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..config import secret, secret_present, settings, update_env
from ..integrations import SOCIAL_PUBLISHING, test_all, write_status_md
from ..marketplaces import ADAPTERS
from ..models import Integration, SocialAccount
from ..ops import audit, session

router = APIRouter()

# key, label, help, is_secret
FIELDS: list[tuple[str, str, str, bool]] = [
    ("CRAYO_API_KEY", "Crayo API key", "Aplikacja Crayo → Developers → Create key (zaczyna się od crayo_sk_).", True),
    ("CRAYO_USD_PER_EXPORT_CREDIT", "Crayo: koszt 1 kredytu eksportu (USD)",
     "Opcjonalne — cena planu ÷ liczba kredytów eksportu. 0 = nieznany.", False),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token", "Telegram → @BotFather → /newbot → skopiuj token.", True),
    ("TELEGRAM_OWNER_ID", "Twoje Telegram ID",
     "Napisz /start do swojego bota, potem kliknij „Wykryj moje ID” poniżej (albo wpisz ręcznie).", False),
    ("DAILY_SPEND_LIMIT_USD", "Limit wydatków dziennie (USD)", "Koszty zmienne (np. kredyty Crayo). Domyślnie 5.", False),
    ("MONTHLY_SPEND_LIMIT_USD", "Limit wydatków miesięcznie (USD)", "Domyślnie 60.", False),
    ("CRAYO_TEST_CLIP_CAP", "Limit klipów testowych Crayo",
     "Ile klipów Crayo zrobi zanim poprosi o zgodę na więcej. Domyślnie 2.", False),
    ("ANTHROPIC_API_KEY", "Anthropic API key (opcjonalnie)",
     "Włącza ocenę momentów przez AI (Claude). console.anthropic.com → API Keys. Koszt ok. $0.05–0.15 na film źródłowy, "
     "liczony w limicie wydatków.", True),
    ("WHOP_USER_TOKEN", "Whop user token (opcjonalnie)", "Tylko dla zadań Whop bounties — można pominąć.", True),
    ("GOOGLE_CLIENT_ID", "Google OAuth client ID (opcjonalnie)", "Tylko do automatycznej publikacji na YouTube.", False),
    ("GOOGLE_CLIENT_SECRET", "Google OAuth client secret (opcjonalnie)", "", True),
]
MARKETPLACES = [m for m in ADAPTERS]


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def missing_essentials() -> list[str]:
    out = []
    if not secret_present("CRAYO_API_KEY"):
        out.append("klucz Crayo")
    if not secret_present("TELEGRAM_BOT_TOKEN"):
        out.append("bot Telegram")
    elif not settings.telegram_owner_id:
        out.append("Twoje Telegram ID")
    with session() as s:
        if not s.scalar(select(SocialAccount).where(SocialAccount.username != "")):
            out.append("nazwy kont społecznościowych")
    return out


def render(msg: str = "") -> str:
    import os
    rows = []
    for key, label, help_, is_secret in FIELDS:
        cur = os.environ.get(key, "")
        if is_secret:
            state = "<b style='color:var(--good)'>ustawione ✓</b>" if cur else "<span style='color:var(--warn)'>brak</span>"
            inp = f"<input type=password name='{key}' autocomplete=off placeholder='{'zostaw puste = bez zmian' if cur else 'wklej tutaj'}'>"
        else:
            state = ""
            inp = f"<input name='{key}' value='{_esc(cur)}' autocomplete=off placeholder='{_esc(help_.split("Domyślnie")[-1].strip(" .") if "Domyślnie" in help_ else "")}'>"
        rows.append(f"<tr><td><b>{label}</b><div class=note>{_esc(help_)}</div></td><td>{inp}</td><td>{state}</td></tr>")
    with session() as s:
        accs = list(s.scalars(select(SocialAccount).order_by(SocialAccount.platform, SocialAccount.label)))
        integ = list(s.scalars(select(Integration).where(Integration.kind != "social").order_by(Integration.kind)))
    acc_rows = []
    for a in accs:
        pub = SOCIAL_PUBLISHING.get(a.platform, {}).get("notes", "")
        acc_rows.append(
            f"<tr><td>{a.label}</td>"
            f"<td><input name='u_{a.id}' value='{_esc(a.username)}' placeholder='@nazwa'></td>"
            f"<td><input name='n_{a.id}' value='{_esc(a.niche)}' placeholder='np. gaming'></td>"
            f"<td><input name='l_{a.id}' value='{_esc(a.language)}' size=3></td>"
            f"<td><input name='m_{a.id}' value='{_esc(','.join(a.marketplaces or []))}' placeholder='content_rewards,clipster'></td>"
            f"<td><select name='s_{a.id}'>" + "".join(
                f"<option {'selected' if a.status == st else ''}>{st}</option>" for st in ("registered", "active", "paused"))
            + f"</select></td><td class=note>{_esc(pub)}</td></tr>")
    with session() as s:
        from ..campaigns import fmt_rate
        from ..models import Campaign
        sel = list(s.scalars(select(Campaign).where(Campaign.selected, Campaign.status == "active")))
        selected_rows = "".join(
            f"<tr><td>{c.id}</td><td><a href='{_esc(c.url)}' target=_blank rel=noopener>{_esc(c.name)}</a>"
            f"<div class=note>{_esc(c.marketplace)}</div></td><td>{_esc(fmt_rate(c))}</td>"
            f"<td class=note>{'<br>'.join(f'<a href={chr(39)}{_esc(u)}{chr(39)} target=_blank rel=noopener>{_esc(u[:60])}</a>' for u in (c.source_links or [])[:4]) or 'zobacz brief'}</td>"
            f"<td><form method=post action='/upload/{c.id}' enctype='multipart/form-data'>"
            f"<input type=file name=file accept='video/*' required> <button>Wgraj</button></form></td>"
            f"<td><form method=post action='/source/{c.id}'><input name=url placeholder='https://drive.google.com/...' size=24>"
            f" <button>Pobierz</button></form></td></tr>" for c in sel) or \
            "<tr><td colspan=6 class=note>Jeszcze nic nie wybrano — pierwszy cykl wyszukiwania trwa kilka minut.</td></tr>"
    int_rows = "".join(f"<tr><td>{i.label}</td><td><b>{i.status}</b></td><td class=note>{_esc(i.account_identifier)}</td>"
                       f"<td class=note>{_esc(i.last_error)}</td></tr>" for i in integ)
    steps = missing_essentials()
    banner = ("<div class=html style='border-color:var(--good)'>✅ Wszystko najważniejsze jest skonfigurowane. "
              "Fabryka działa sama — sprawdzaj Telegram.</div>") if not steps else \
        f"<div class=html style='border-color:var(--warn)'>Do zrobienia: <b>{', '.join(steps)}</b>.</div>"
    return f"""
{f"<div class=html>{_esc(msg)}</div>" if msg else ""}
{banner}
<section><h2>1. Klucze i limity</h2><form method=post action='/setup/save'>
<table>{''.join(rows)}</table>
<div style='padding:10px 12px'><button>Zapisz i przetestuj</button></div></form>
<div style='padding:0 12px 12px'><form method=post action='/setup/telegram-detect' style='display:inline'>
<button>Wykryj moje Telegram ID</button></form>
<span class=note> — najpierw wyślij <code>/start</code> do swojego bota w Telegramie.</span></div></section>

<section><h2>2. Konta społecznościowe (11)</h2>
<div class=note>Wpisz nazwy kont. Marketplaces = gdzie konto jest podpięte (np. content_rewards,clipster,vyro).
Konta bez nazwy liczą się jako „połowicznie dostępne” przy wyborze kampanii.</div>
<form method=post action='/setup/accounts'><table>
<tr><th>Konto</th><th>Nazwa</th><th>Nisza</th><th>Język</th><th>Marketplaces</th><th>Status</th><th>Publikacja przez API</th></tr>
{''.join(acc_rows)}</table><div style='padding:10px 12px'><button>Zapisz konta</button></div></form></section>

<section><h2>3. Wybrane kampanie — dołącz i wgraj materiał źródłowy</h2>
<div class=note>Fabryka sama wybiera najlepsze kampanie. Dla każdej: (1) dołącz w marketplace, (2) przeczytaj pełne zasady,
(3) wgraj oficjalny materiał z briefu (plik albo bezpośredni link). Resztę — klipy, QC, plan publikacji — zrobi sama.</div>
<table><tr><th>#</th><th>Kampania</th><th>Stawka</th><th>Materiał wskazany w briefie</th><th>Wgraj plik</th><th>…albo link</th></tr>
{selected_rows}</table></section>

<section><h2>4. Status połączeń</h2><table>
<tr><th>Usługa</th><th>Status</th><th>Info</th><th>Błąd</th></tr>{int_rows}</table>
<div style='padding:10px 12px'><form method=post action='/setup/test' style='display:inline'><button>Testuj połączenia</button></form></div>
</section>
<div class=note>Klucze są zapisywane w pliku <code>.env</code> na tym komputerze i nigdy nie są tu wyświetlane.</div>
"""


def _auth_dep():
    from .app import auth
    return auth


@router.post("/setup/save")
async def save(request: Request, _: None = Depends(_auth_dep())):
    form = await request.form()
    changed = update_env({k: str(form.get(k, "")) for k, *_ in FIELDS})
    with session() as s:
        audit(s, "owner", "setup_saved", keys=changed)   # key NAMES only, never values
        results = test_all(s)
        write_status_md(s)
    summary = ", ".join(f"{k}: {v}" for k, v in results.items() if k in ("Crayo", "Telegram bot"))
    return RedirectResponse(f"/s/setup?msg=Zapisano: {', '.join(changed) or 'bez zmian'}. {summary}", status_code=303)


@router.post("/setup/accounts")
async def accounts(request: Request, _: None = Depends(_auth_dep())):
    form = await request.form()
    with session() as s:
        for a in s.scalars(select(SocialAccount)):
            a.username = str(form.get(f"u_{a.id}", a.username)).strip().lstrip("@")
            a.niche = str(form.get(f"n_{a.id}", a.niche)).strip()
            a.language = (str(form.get(f"l_{a.id}", a.language)).strip() or "en").lower()[:8]
            a.marketplaces = [m.strip() for m in str(form.get(f"m_{a.id}", "")).split(",") if m.strip() in MARKETPLACES]
            st = str(form.get(f"s_{a.id}", a.status))
            a.status = st if st in ("registered", "active", "paused") else a.status
        audit(s, "owner", "accounts_updated")
        from ..campaigns import rescore_all
        rescore_all(s)
    return RedirectResponse("/s/setup?msg=Konta zapisane, kampanie przeliczone.", status_code=303)


@router.post("/setup/test")
def test(_: None = Depends(_auth_dep())):
    with session() as s:
        results = test_all(s)
        write_status_md(s)
    return RedirectResponse("/s/setup?msg=" + "; ".join(f"{k}: {v}" for k, v in results.items()), status_code=303)


@router.post("/setup/telegram-detect")
def telegram_detect(_: None = Depends(_auth_dep())):
    tok = secret("TELEGRAM_BOT_TOKEN")
    if not tok:
        return RedirectResponse("/s/setup?msg=Najpierw zapisz token bota.", status_code=303)
    try:
        r = httpx.get(f"https://api.telegram.org/bot{tok}/getUpdates", params={"timeout": 0}, timeout=20)
        msgs = [u.get("message") for u in r.json().get("result", []) if u.get("message")]
    except (httpx.HTTPError, ValueError):
        return RedirectResponse("/s/setup?msg=Nie udało się połączyć z Telegramem — sprawdź token.", status_code=303)
    private = [m for m in msgs if (m.get("chat") or {}).get("type") == "private"]
    if not private:
        return RedirectResponse("/s/setup?msg=Nie widzę wiadomości. Wyślij /start do bota i kliknij ponownie.",
                                status_code=303)
    who = private[-1]["from"]
    update_env({"TELEGRAM_OWNER_ID": str(who["id"])})
    from ..notify import telegram_send
    telegram_send("✅ Połączono z Clipping Factory. Od teraz tylko Ty możesz sterować botem. Wpisz /help.")
    with session() as s:
        audit(s, "owner", "telegram_owner_detected")
    name = who.get("username") or who.get("first_name", "")
    return RedirectResponse(f"/s/setup?msg=Zapisano Telegram ID dla {name}. Sprawdź wiadomość od bota.", status_code=303)


VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


@router.post("/upload/{campaign_id}")
async def upload(campaign_id: int, request: Request, _: None = Depends(_auth_dep())):
    from pathlib import Path

    from ..models import Campaign
    from ..ops import enqueue
    form = await request.form()
    f = form.get("file")
    with session() as s:
        c = s.get(Campaign, campaign_id)
        if c is None or f is None or not getattr(f, "filename", ""):
            return RedirectResponse("/s/setup?msg=Brak pliku albo kampanii.", status_code=303)
        ext = Path(f.filename).suffix.lower()
        if ext not in VIDEO_EXT:
            return RedirectResponse("/s/setup?msg=To nie jest plik wideo (mp4/mov/mkv/webm).", status_code=303)
        d = settings.data_dir / "inbox" / str(campaign_id)
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in Path(f.filename).stem if ch.isalnum() or ch in "-_")[:60] or "source"
        dst = d / f"{safe}{ext}"
        with dst.open("wb") as out:
            while chunk := await f.read(1 << 20):
                out.write(chunk)
        audit(s, "owner", "source_uploaded", "campaign", campaign_id, file=dst.name)
        enqueue(s, "inbox", {}, dedupe_key=f"inbox:upload:{campaign_id}:{dst.name}")
    return RedirectResponse(f"/s/setup?msg=Wgrano {dst.name} — produkcja klipów rusza automatycznie.", status_code=303)


@router.post("/source/{campaign_id}")
async def source_link(campaign_id: int, request: Request, _: None = Depends(_auth_dep())):
    from ..ops import enqueue
    url = str((await request.form()).get("url", "")).strip()
    if not url.startswith("https://"):
        return RedirectResponse("/s/setup?msg=Podaj link zaczynający się od https://", status_code=303)
    with session() as s:
        enqueue(s, "fetch_source", {"campaign_id": campaign_id, "url": url})
    return RedirectResponse("/s/setup?msg=Pobieranie dodane do kolejki.", status_code=303)


# ------------------------------------------------------------------ to-do page
def render_todo(msg: str = "") -> str:
    from ..models import Approval, Campaign, Clip, Submission
    from ..qc import youtube_title
    with session() as s:
        accs = {a.id: a for a in s.scalars(select(SocialAccount))}
        camps = {c.id: c for c in s.scalars(select(Campaign))}
        pub = list(s.scalars(select(Submission).where(Submission.status == "ready_to_publish")
                             .order_by(Submission.scheduled_for)))
        sub = list(s.scalars(select(Submission).where(Submission.status == "awaiting_submission")
                             .order_by(Submission.submission_deadline_at)))
        appr = list(s.scalars(select(Approval).where(Approval.status == "pending")))
        clips = {c.id: c for c in s.scalars(select(Clip).where(Clip.id.in_([x.clip_id for x in pub])))}
    pub_rows = []
    for x in pub:
        a, c, cl = accs[x.account_id], camps[x.campaign_id], clips.get(x.clip_id)
        title = f"<div class=note>Tytuł YT: {_esc(youtube_title(cl))}</div>" if cl and x.platform == "youtube" else ""
        pub_rows.append(
            f"<tr><td>#{x.id}</td><td>{_esc(x.platform)}<div class=note>@{_esc(a.username or a.label)}</div></td>"
            f"<td>{_esc(c.name[:50])}</td><td><a href='/clip/{x.clip_id}.mp4' download>⬇ pobierz</a></td>"
            f"<td><textarea rows=3 cols=34 readonly onclick='this.select()'>{_esc(x.caption)}</textarea>{title}</td>"
            f"<td><form method=post action='/todo/posted/{x.id}'><input name=url placeholder='link do posta' size=22 required>"
            f" <button>Opublikowane</button></form></td></tr>")
    sub_rows = []
    for x in sub:
        c = camps[x.campaign_id]
        dl = f"{x.submission_deadline_at:%d.%m %H:%M} UTC" if x.submission_deadline_at else "wg briefu"
        sub_rows.append(
            f"<tr><td>#{x.id}</td><td><a href='{_esc(x.published_url)}' target=_blank rel=noopener>post</a></td>"
            f"<td><a href='{_esc(c.url)}' target=_blank rel=noopener>{_esc(c.name[:50])}</a><div class=note>{_esc(c.marketplace)}</div></td>"
            f"<td><b>{dl}</b></td><td><form method=post action='/todo/submitted/{x.id}'><button>Zgłoszone</button></form></td></tr>")
    appr_rows = [f"<tr><td>#{p.id}</td><td>{_esc(p.kind)}</td><td>{_esc(p.summary)}</td>"
                 f"<td><form method=post action='/action/approval/{p.id}' style='display:inline'>"
                 f"<button name=decision value=approved>Zgoda</button> <button class=bad name=decision value=rejected>Odrzuć</button>"
                 f"</form></td></tr>" for p in appr]
    empty = lambda n: f"<tr><td colspan={n} class=note>Nic do zrobienia.</td></tr>"  # noqa: E731
    return f"""
{f"<div class=html>{_esc(msg)}</div>" if msg else ""}
<section><h2>1. Do opublikowania ({len(pub)})</h2><div class=note>Pobierz klip, wrzuć na wskazane konto z tym opisem,
wklej link do posta. (Te same pliki przychodzą też na Telegram.)</div>
<table><tr><th>#</th><th>Konto</th><th>Kampania</th><th>Plik</th><th>Opis (kliknij = zaznacz)</th><th>Link</th></tr>
{''.join(pub_rows) or empty(6)}</table></section>
<section><h2>2. Do zgłoszenia w marketplace ({len(sub)})</h2><div class=note>Wklej link posta w kampanii przed terminem.</div>
<table><tr><th>#</th><th>Post</th><th>Kampania</th><th>Termin</th><th></th></tr>{''.join(sub_rows) or empty(5)}</table></section>
<section><h2>3. Zgody ({len(appr)})</h2><table>{''.join(appr_rows) or empty(4)}</table></section>
"""


@router.post("/todo/posted/{sid}")
async def todo_posted(sid: int, request: Request, _: None = Depends(_auth_dep())):
    from ..models import Submission
    from ..pipeline import mark_published
    url = str((await request.form()).get("url", "")).strip()
    if not url.startswith("https://"):
        return RedirectResponse("/s/todo?msg=Link musi zaczynać się od https://", status_code=303)
    with session() as s:
        x = s.get(Submission, sid)
        try:
            mark_published(s, x, url, "confirmed by owner via dashboard")
        except ValueError as e:
            return RedirectResponse(f"/s/todo?msg={_esc(str(e))}", status_code=303)
    return RedirectResponse(f"/s/todo?msg=#{sid} opublikowane — teraz zgłoś go w kampanii.", status_code=303)


@router.post("/todo/submitted/{sid}")
def todo_submitted(sid: int, _: None = Depends(_auth_dep())):
    from ..models import Submission
    from ..pipeline import mark_submitted
    with session() as s:
        mark_submitted(s, s.get(Submission, sid), "confirmed by owner via dashboard")
    return RedirectResponse(f"/s/todo?msg=#{sid} zgłoszone. Fabryka śledzi wyświetlenia.", status_code=303)


def todo_count() -> int:
    from ..models import Approval, Submission
    from sqlalchemy import func
    with session() as s:
        return (s.scalar(select(func.count()).select_from(Submission).where(
            Submission.status.in_(["ready_to_publish", "awaiting_submission"]))) or 0) + \
            (s.scalar(select(func.count()).select_from(Approval).where(Approval.status == "pending")) or 0)
