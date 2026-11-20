# TASKS

## Waiting on the owner (one at a time, in this order)
1. **Crayo API key** — Crayo app → Developers → create key → put `CRAYO_API_KEY=` in `.env` on the server. Then `factory integrations --test`.
2. **Telegram bot** — @BotFather `/newbot` → `TELEGRAM_BOT_TOKEN`; your numeric ID (@userinfobot) → `TELEGRAM_OWNER_ID`.
3. **Account usernames** — for each of the 11 accounts: `factory set-account tiktok-1 --username yourhandle --niche gaming --language en --marketplaces content_rewards,clipster`.
4. **Join selected campaigns** on Content Rewards (links come via Telegram) and drop the brief's source video into `data/inbox/<campaign_id>/` (or `/source <id> <url>`).
5. **Server** for 24/7 operation (any small VPS with 4 GB RAM; see README → Deploy).
6. Optional: Whop OAuth app + `WHOP_USER_TOKEN`; Google OAuth client for YouTube publishing; re-authorise Google Drive and upload the Mussy.02 / Starter Kit materials.

## Engineering backlog
- [ ] Verify Crayo: account → upload → AutoClip (2 clips) → export → QC; learn the AutoClip credit type.
- [ ] Verify Crayo webhook signature format with `POST /v1/webhooks/{id}/test`; add a webhook endpoint to the dashboard.
- [ ] First real end-to-end run on one Content Rewards campaign with one TikTok account (manual publish/submit).
- [ ] Optional LLM re-ranking of moment candidates (Claude API) once the heuristic baseline has data.
- [ ] Clipster/Vyro: assisted submission through the owner's own logged-in browser (no credential storage).
- [ ] YouTube upload test on an owner channel (check privacy lock on unverified project).
- [ ] Per-platform caption variants (hashtag counts, title length).
