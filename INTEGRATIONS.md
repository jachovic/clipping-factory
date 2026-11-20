# INTEGRATIONS — platform audit

Audited 2026-11-20 from each platform's **own** published material (llms.txt, OpenAPI specs, help centres, robots.txt).
Nothing below is inferred from marketing blogs. Live status per connection: see `INTEGRATION_STATUS.md` (generated).

Legend: **auto** verified live · **auto_untested** built from official docs, needs credentials · **manual** human task via Telegram.

## Campaign marketplaces

### Content Rewards — contentrewards.com
- Operator: Content Rewards Inc. (Beverly Hills). Its FAQ: *"Is Content Rewards part of Whop? No."* It started on Whop in
  late 2024 and campaigns can still be joined inside Whop.
- **API:** none documented. `robots.txt` disallows `/api/`; we do not touch it.
- **Discovery: auto (verified).** Public pages `/c/discover` and `/c/campaigns/<id>` embed the campaign data
  (React Query payload). We read 27 live campaigns with: title, brand, category, CPM per platform (`payoutModel.rate`
  in cents per 1k views), min/max payout per video, total/available budget, platforms, application requirement, and
  counts of rules/references visible only after login. Polite rate: 1 request / 2 s.
- **Gotcha found:** the discover card can list platforms that pay **$0** on the detail page (e.g. ClipUp lists 3, pays
  only Instagram). The adapter always enriches from the detail page before scoring.
- **Join / submit / review status / earnings: manual** (logged-in app at contentrewards.com/login).
- Facts from FAQ: every submission is reviewed by the brand (nothing auto-approves); CPM payouts in rounds of
  7 days earning + 3-day hold (~every 10 days); a clip usually earns for 30 days after submission; each submission
  gets a bot-likelihood score; brands pay a 10% platform fee (not deducted from creators per the FAQ).

### Clipster — clipster.com (app: app.clipster.gg)
- **API:** none published. `llms-full.txt` help centre read in full.
- Creators link TikTok/Instagram/YouTube via the platform's own sign-in ("Link Account"); bio-verified accounts are
  excluded from some (e.g. country-targeted) campaigns.
- **Submit the post link within 24 hours of posting.** Only views gained after submission count. Views refresh every few hours.
- Views must be organic: no bots, ads or engagement groups. Paid posts must stay public **30 days after payout**.
- Payout ~1 week after campaign end; **$50 minimum withdrawal** (PayPal, Visa prepaid, Amazon GC, USDT, bank).
  No country block; VPN allowed. Discord needed for support tickets.
- **All operations: manual** until we can use a logged-in browser session the owner controls.
- Network note: `app.clipster.gg` / `www.clipster.gg` are not yet on this environment's allowlist.

### Vyro — vyro.com (app: app.vyro.com)
- Owned by Beast Industries (MrBeast). Official statement in `llms.txt`: *"Vyro does not currently publish a public API,
  OpenAPI spec, webhooks, or an MCP server."*
- **Discovery: auto (verified)** from `vyro.com/campaigns.md` (every page has a markdown version). Rates are
  **per 1M views** ($1,400–$2,000 / 1M = $1.40–$2.00 CPM). "Locked for new posts" detected. Budget and paid platforms
  are not on the public board, so Vyro campaigns are capped at MEDIUM until confirmed in the app.
- Join / submit / earnings: manual (app.vyro.com).

### Whop — whop.com
- **Official REST API** `https://api.whop.com/api/v1` (OpenAPI at `/api/v1/openapi.json`, 7.7 MB, read directly).
- `GET /bounties` — for a *user token*, lists bounties the user can see and work (filters: status, country, query).
- `POST /bounty_submissions` `{bounty_id, deliverable:{urls}}` — *"Requires a user credential — account API keys cannot
  author submissions."* User credential = **OAuth 2.1 + PKCE** ("Sign in with Whop", endpoints under `/oauth/`).
- Bounties are **fixed reward per accepted submission** (`net_reward_amount`, `spots_remaining`), not CPM.
  CPM clipping campaigns live on Content Rewards, which is a separate company.
- Status: **auto_untested** — needs `WHOP_USER_TOKEN` (owner authorises a Whop OAuth app once).

### Promote.fun / Prime Oracles
- Both are JavaScript-only apps; no API, docs or `llms.txt` content found. Prime Oracles describes itself as a
  "digital products marketplace and content rewards". **Manual**; scheduled for Phase 8 research with a logged-in session.

## Crayo — crayo.ai (API: api.crayo.ai)
Crayo **has a public REST API** (OpenAPI 3.1, 68 endpoints, read in full from `crayo.ai/docs/openapi.json`):
- Auth: `Authorization: Bearer crayo_sk_…`, key created on the **Developers page** of the Crayo app.
- `GET /v1/account`: plan, credit balances (export, voiceover, image, video, tracker), storage, rate limits.
- Upload: import from public URL (≤100 MB) or signed upload (video ≤1 GB).
- **AutoClip** `POST /v1/autoclip`: source 1 min–3 h; 2–20 clips; 30/60/90 s; edit level clips|captions|cropped|full;
  prompt; speech|energy mode; 50+ languages. Credits charged for the clips requested, at job start.
- **Export** `POST /v1/projects/{id}/export`: **1 export credit per second** of finished video, charged when queued.
- Transcripts: 1 voiceover credit/second (cached per asset). Webhooks for export/autoclip/tracker events.
- **View Tracker**: live post stats for Instagram/YouTube/TikTok/Twitter/Facebook, 1 tracker credit per lookup — we use
  it for public view counts (which are **not** eligible views).
- Not in the spec: which credit type AutoClip consumes, whether our plan includes API access, the webhook signature
  scheme. The factory measures credit balances before/after the first job and logs the difference.
- Status: **auto_untested** until `CRAYO_API_KEY` is set. First run is capped at **2 clips** (`CRAYO_TEST_CLIP_CAP`).

## Social publishing (official APIs only)
> These constraints are from the platforms' developer documentation as I know it. Re-verify each at setup time,
> because they change often.

| Platform | Official route | Key constraint |
|---|---|---|
| YouTube | Data API v3 `videos.insert` (OAuth `youtube.upload`) | Uploads from an unverified API project are locked **private** until Google's audit. Implemented; untested. |
| TikTok | Content Posting API, Direct Post (`video.publish`) | Unaudited apps can only post **SELF_ONLY** (private). Needs a TikTok developer app and audit. |
| Instagram | Graph API content publishing (Reels) | **Professional (Business/Creator)** accounts only; the video must be at a public URL. |
| X | API v2 posts + media upload | Write access depends on the paid developer tier. |
| Facebook | Page `video_reels` | Pages only. |

Until an account's API route is authorised **and verified public**, publishing is a **manual task**: the bot sends
you the file path and the exact caption, and you confirm with `/posted <id> <url>`. Nothing is marked published
without a URL.

## Other
- **Telegram Bot API**: long polling; only `TELEGRAM_OWNER_ID` is obeyed; others are logged and ignored.
- **Transcription**: faster-whisper (`base`, int8, CPU), model downloaded from Hugging Face (needs `us.aws.cdn.hf.co`,
  `HF_HUB_DISABLE_XET=1`).
- **Mussy.02 course / AI Clipping Agency Starter Kit**: not accessible here (paid, not uploaded). Their contents were
  not assumed. Drop them into the repo or Google Drive (the Drive connector needs re-authorising) and they will be analysed.
