# TASKS

## For you — short version: see START_TUTAJ.md (install → paste keys → enter account names)

## For you — detailed (ordered by impact, none need Claude)
1. **Install on your PC** (15 min): download the repo ZIP from GitHub → unzip to `C:\clipping-factory` → in PowerShell
   there: `powershell -ExecutionPolicy Bypass -File windows\install.ps1` → open http://127.0.0.1:8080.
   If anything errors, save the message for the next session.
2. **Create the Telegram bot** (5 min): Telegram → @BotFather → `/newbot` → copy the token into `.env` as
   `TELEGRAM_BOT_TOKEN=`. Message @userinfobot → copy your number into `TELEGRAM_OWNER_ID=`. Then restart:
   `Stop-ScheduledTask ClippingFactory; Start-ScheduledTask ClippingFactory`.
3. **Crayo API key** (5 min): Crayo app → Developers → create key → `.env`: `CRAYO_API_KEY=`. Check whether your plan
   includes API credits (Developers page / account). Also note your monthly price for: `factory subscription crayo <price>`.
4. **Register your 11 accounts** (10 min), one line each, e.g.
   `.venv\Scripts\factory set-account tiktok-1 --username yourhandle --niche gaming --language en --marketplaces content_rewards`
   (labels: tiktok-1..5, instagram-1..2, x-1..2, youtube-1..2). Note which accounts are Instagram **Business/Creator**.
5. **Sign up / log in on Content Rewards** (contentrewards.com/login) and link your TikTok/Instagram/YouTube there.
   Do the same on **Clipster** (app.clipster.gg → Settings → Connected Accounts → Link Account; also connect Discord).
6. **Join 1–2 top campaigns** (see the dashboard → Campaign Intelligence, or `factory campaigns`), e.g. Mumford & Sons or
   SEGA trailer. Read the full rules (some are only visible after login) and download the **official source video**
   from the brief into `data\inbox\<campaign id>\`. The factory ingests and clips it automatically.
7. **Upload the course materials** (Mussy.02 guide, AI Clipping Agency Starter Kit) to Google Drive and re-authorise the
   Google Drive connector on claude.ai, or put them in a `docs/materials/` folder in the repo. I'll extract the
   techniques next session.
8. **Power settings**: Windows → Settings → System → Power → Sleep: Never (plugged in).

## Next engineering priorities (next session)
- [ ] Verify Crayo: account → upload → AutoClip (2 clips) → export → QC; learn the AutoClip credit type; compare with local engine.
- [ ] First real end-to-end run: one campaign, one TikTok account, manual publish + submit via Telegram.
- [ ] Fix anything the Windows installer hits.
- [ ] Apply techniques from the course materials to hooks/captions/moment scoring.
- [ ] Per-platform caption variants; optional Claude-based moment re-ranking.
- [ ] Clipster/Vyro assisted submission via your own logged-in browser.
