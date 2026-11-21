"""Command line: `factory <command>`."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy import select


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="factory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create tables, seed integrations/accounts/experiments")
    d = sub.add_parser("discover", help="run campaign discovery now")
    d.add_argument("--marketplace", action="append")
    c = sub.add_parser("campaigns", help="list campaigns by score")
    c.add_argument("--all", action="store_true")
    i = sub.add_parser("integrations", help="show integration status")
    i.add_argument("--test", action="store_true", help="run live connection tests")
    pr = sub.add_parser("produce", help="produce clips for a campaign from a local source file")
    pr.add_argument("--campaign", type=int, required=True)
    pr.add_argument("--file", required=True)
    pr.add_argument("--rights", required=True, help="who granted use, e.g. 'campaign brief <url>'")
    pr.add_argument("-n", type=int, default=2)
    sa = sub.add_parser("set-account", help="update a social account record")
    sa.add_argument("label")
    for f in ("username", "language", "niche", "geography", "status", "publish_method", "account_type"):
        sa.add_argument(f"--{f.replace('_', '-')}", dest=f)
    sa.add_argument("--marketplaces", help="comma-separated marketplaces this account is linked on")
    ak = sub.add_parser("account-kit", help="prepare a profile kit for a NEW legitimate account")
    ak.add_argument("--platform", required=True)
    ak.add_argument("--niche", required=True)
    ak.add_argument("--language", default="en")
    sb = sub.add_parser("subscription", help="set a monthly subscription cost, e.g. subscription crayo 29.99 (0 removes)")
    sb.add_argument("name")
    sb.add_argument("usd", type=float)
    rp = sub.add_parser("costs", help="unit economics: cost per clip / approved clip / 1k eligible views")
    rp.add_argument("--days", type=int, default=0)
    sub.add_parser("worker", help="run the scheduler + job worker (long-running)")
    sub.add_parser("bot", help="run the Telegram bot (long-running)")
    w = sub.add_parser("web", help="run the dashboard")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8080)
    r = sub.add_parser("run", help="all-in-one: worker + Telegram bot (if configured) + dashboard (for a single PC)")
    r.add_argument("--port", type=int, default=8080)
    sub.add_parser("report", help="print the daily report")
    sub.add_parser("backup", help="back up the database now")
    a = p.parse_args(argv)

    from .models import init_db
    from .ops import session
    init_db()

    if a.cmd == "init":
        from .growth import seed_experiments
        from .integrations import seed, write_status_md
        with session() as s:
            seed(s)
            seed_experiments(s)
            write_status_md(s)
        print("initialised")
    elif a.cmd == "discover":
        from .campaigns import discover_all
        with session() as s:
            print(json.dumps(discover_all(s, a.marketplace), indent=2))
    elif a.cmd == "campaigns":
        from .campaigns import fmt_rate
        from .models import Campaign
        with session() as s:
            q = select(Campaign).order_by(Campaign.score.desc().nullslast())
            if not a.all:
                q = q.where(Campaign.status == "active")
            for c in s.scalars(q):
                ex = (c.profitability or {}).get("expected", {})
                print(f"#{c.id:<4} {c.priority:<6} {c.score or 0:5.1f}  {c.marketplace:<16} {fmt_rate(c):<18} "
                      f"budget ${c.budget_available_usd or 0:>9,.0f}  exp.net ${ex.get('net_usd', 0):>7}  {c.name[:55]}")
    elif a.cmd == "integrations":
        from .integrations import seed, test_all, write_status_md
        with session() as s:
            seed(s)
            if a.test:
                print(json.dumps(test_all(s), indent=2))
            print(write_status_md(s))
    elif a.cmd == "produce":
        from . import pipeline
        from .production import ingest_source
        with session() as s:
            src = ingest_source(s, a.file, rights=a.rights, campaign_id=a.campaign)
            print(json.dumps(pipeline.produce(s, a.campaign, src.id, a.n), indent=2, default=str))
    elif a.cmd == "set-account":
        from .models import SocialAccount
        with session() as s:
            acc = s.scalar(select(SocialAccount).where(SocialAccount.label == a.label))
            if acc is None:
                sys.exit(f"no account {a.label}")
            for f in ("username", "language", "niche", "geography", "status", "publish_method", "account_type"):
                if getattr(a, f):
                    setattr(acc, f, getattr(a, f))
            if a.marketplaces:
                acc.marketplaces = [m.strip() for m in a.marketplaces.split(",") if m.strip()]
            print(f"updated {acc.label}")
    elif a.cmd == "account-kit":
        print(account_kit(a.platform, a.niche, a.language))
    elif a.cmd == "subscription":
        from .ops import get_flag, set_flag
        with session() as s:
            subs = json.loads(get_flag(s, "subscriptions", "{}") or "{}")
            if a.usd > 0:
                subs[a.name] = a.usd
            else:
                subs.pop(a.name, None)
            set_flag(s, "subscriptions", json.dumps(subs))
            print("monthly subscriptions:", subs)
    elif a.cmd == "costs":
        import datetime as dt
        from .manager import cost_metrics
        from .models import now
        with session() as s:
            print(json.dumps(cost_metrics(s, now() - dt.timedelta(days=a.days) if a.days else None), indent=2))
    elif a.cmd == "worker":
        from .manager import worker_loop
        worker_loop()
    elif a.cmd == "bot":
        from .telegram_bot import run_bot
        run_bot()
    elif a.cmd == "web":
        import uvicorn
        uvicorn.run("factory.dashboard.app:app", host=a.host, port=a.port, proxy_headers=True)
    elif a.cmd == "run":
        run_all(a.port)
    elif a.cmd == "report":
        from .manager import daily_report
        with session() as s:
            print(daily_report(s))
    elif a.cmd == "backup":
        from .backup import backup_db
        print(backup_db())


def run_all(port: int) -> None:
    """Single-machine mode. Each component runs in a supervised thread that restarts after a crash."""
    import threading
    import time

    import uvicorn

    from .config import secret_present
    from .growth import seed_experiments
    from .integrations import seed, write_status_md
    from .manager import worker_loop
    from .ops import session

    with session() as s:
        seed(s)
        seed_experiments(s)
        write_status_md(s)

    def supervised(name, fn):
        def loop():
            while True:
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    logging.getLogger("factory").exception("%s crashed; restarting in 30 s", name)
                    time.sleep(30)
        threading.Thread(target=loop, name=name, daemon=True).start()

    supervised("worker", worker_loop)

    def telegram_when_configured():
        # the bot starts as soon as a token is saved on the setup page — no restart needed
        while not secret_present("TELEGRAM_BOT_TOKEN"):
            time.sleep(15)
        from .telegram_bot import run_bot
        run_bot()
    supervised("telegram", telegram_when_configured)
    print(f"Dashboard: http://127.0.0.1:{port}  (setup: http://127.0.0.1:{port}/s/setup)")
    uvicorn.run("factory.dashboard.app:app", host="127.0.0.1", port=port)


def account_kit(platform: str, niche: str, language: str) -> str:
    """Profile kit for a new account run by a real operator — a clearly branded clip/highlights page,
    never an impersonation of the source creator or a fake person."""
    n = niche.strip().lower().replace(" ", "")
    names = [f"{n}clips", f"{n}.highlights", f"daily{n}moments", f"{n}cuts"]
    return "\n".join([
        f"Platform: {platform} · niche: {niche} · language: {language}",
        "Username ideas (check availability; do not imitate any creator's name or logo):",
        *(f"  - {x}" for x in names),
        f"Bio: Best {niche} moments, clipped daily. Fan/highlights page — not affiliated unless stated.",
        "Profile image: simple wordmark of the page name on a solid colour (no creator photos/logos).",
        "Content plan (first 2 weeks): 1–2 clips/day from campaigns in this niche; keep the niche consistent.",
        "Rules: create & verify the account yourself via the platform's normal sign-up; one real operator; "
        "follow the platform's rules on multiple accounts; add campaign-required disclosures.",
    ])


if __name__ == "__main__":
    main()
