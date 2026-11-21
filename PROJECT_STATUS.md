# PROJECT_STATUS — 2026-11-21 (session 3)

**Session 3:** see `SESSION_REPORT.md`. Sitemap-based full Content Rewards coverage (97 tracked, 24 HIGH), UGC exclusion,
Vyro platforms, cost metrics + weekly/monthly reports, CI. Your to-do list is at the top of `TASKS.md`.

# Session 2

**Session 2:** added single-PC mode (`factory run`), Windows installer with autostart, brief-link extraction for
source footage. Factory running continuously in the cloud container as a *temporary* server (state lost when the
container is recycled). Live: 29 active campaigns, 10 HIGH, 4 selected.

---


## Bottom line
The factory's software is built and tested. Campaign discovery works on **live** data, and the local clip engine
produces QC-passed 9:16 clips. **No real clip has been published, submitted or paid yet.** That needs your keys, your
logins and a server. Real earnings to date: **$0.00**.

## Environment audit
| Item | Finding |
|---|---|
| Container | Ephemeral cloud container (4 CPU, 15 GB RAM). Python 3.13, Node 22, FFmpeg 6.1 (libass), Postgres 16, Redis 7, Docker, Playwright + Chromium |
| Network | Was blocked for all platforms. Created personal environment **ai-clipping-factory** that allows only the 19 hosts needed (marketplaces, Crayo, social APIs, Telegram, model CDN) |
| Your computer / browser | **Not attached.** No logged-in browser, so no OAuth or web logins could be performed from here |
| Existing project | None (empty workspace), so nothing was overwritten |
| Paid course materials | Not available here; Google Drive connector needs re-authorising |
| Persistence | Code is committed to a **local** git repo only. It must be pushed to GitHub before this container is reclaimed |

## What works (tested)
- **Campaign discovery, live:** Content Rewards (27 campaigns) and Vyro (7), with change detection, closures, low-budget events.
- **Scoring 0–100 + 4-scenario profitability** on real briefs: 7 HIGH / 21 MEDIUM / 2 LOW. Profitability accounts for min-payout thresholds (e.g. SypherPK needs 10k views per clip before earning, so it is demoted), per-video caps and remaining budget.
- **Selection + account assignment:** top 4 HIGH campaigns selected; accounts spread across platforms (max 3 per campaign, 3 campaigns per account); inbox folders created.
- **Local production:** 8-minute test source → Whisper transcription (1,148 words) → moment detection → two 1080×1920 clips (34 s, 59 s) with hook banner + word-highlighted captions → QC 100/100. Frames visually checked.
- **QC:** resolution/aspect/duration/audio levels, caption coverage/sync/safe zone, sentence boundaries, rights, campaign hashtags/disclosures, duplicate fingerprinting, learned rules.
- **Job queue / worker:** one full live cycle ran (discover, select, inbox, deadlines, metrics, integrations, growth, backup).
- **Dashboard:** all 21 sections render on live data. **Telegram commands:** tested offline (owner-only enforcement verified).
- **22 automated tests pass.**

## Built from official docs, not yet verified (needs credentials)
Crayo API client (AutoClip, export, transcripts, View Tracker, webhooks), Whop bounties (OAuth user token),
YouTube upload, Telegram live messaging.

## Connected accounts
**None yet.** The 11 social accounts are registered as `tiktok-1..5`, `instagram-1..2`, `x-1..2`, `youtube-1..2`
without usernames.

## Requires your approval / action
1. Push target: a GitHub repo to push this project to (otherwise it is lost when the container is recycled).
2. Crayo API key (and confirm your plan includes API access).
3. Telegram bot token + your Telegram ID.
4. Usernames, niche and language for the 11 accounts.
5. A server for 24/7 operation.
6. Spending limits: defaults are $5/day and $60/month for variable costs; Crayo is capped at 2 test clips.

## Next three priorities
1. Crayo key → 2-clip AutoClip test on a real campaign's official footage → compare with the local engine.
2. First real end-to-end run: one HIGH Content Rewards campaign, one TikTok account, manual publish + submit via Telegram.
3. Deploy with docker compose on a VPS so the loop runs 24/7.
