# ARCHITECTURE

**Decision:** one Python 3.11+ package, PostgreSQL in production (SQLite for dev/tests), and **no Redis**. The job queue
lives in the database (optimistic row locking + stale-lock recovery), which is simpler and survives crashes. If one
worker ever becomes the bottleneck, run more `worker` containers; the claim logic is already multi-worker safe.

```
             ┌────────────── factory worker (scheduler + job runner, restarts automatically) ─────────────┐
 DISCOVER ─► campaigns.discover_all ─► marketplaces/* adapters ─► upsert + change events ─► score + profitability
 SELECT   ─► manager.h_select ─► accounts.assign_accounts ─► inbox/<campaign>/ + Telegram "join & add source"
 PRODUCE  ─► manager.h_inbox / h_fetch_source ─► production.ingest_source (rights note REQUIRED)
            pipeline.produce ─► Crayo AutoClip (if key, spend guard, 2-clip test cap) ─or─ local engine:
            faster-whisper ─► moment detection (10 criteria) ─► FFmpeg 1080x1920 render + word-synced captions
 QC       ─► qc.run_qc (technical, captions, rights, campaign rules, duplicates, learned rules) ─► 0-100 + decision
 PUBLISH  ─► pipeline.plan_submissions (dedupe) ─► publishing.* (official API or manual Telegram task)
 SUBMIT   ─► marketplace adapter submit (API where it exists, else Telegram task + deadline tracker)
 MONITOR  ─► Crayo View Tracker (public views), marketplace figures (eligible views/earnings), deadlines
 OPTIMIZE ─► growth.growth_review (medians, n>=8 per group, experiments)
             └──────────────────────────────────────────────────────────────────────────────────────────────┘
 telegram bot (owner-only commands)      dashboard (FastAPI, token)      PostgreSQL = source of truth
```

## Modules (`factory/`)
| Module | Responsibility |
|---|---|
| `config.py` | env/.env settings; secrets read from env only |
| `models.py` | schema: integrations, campaigns (+events), sources, clips, accounts, assignments, submissions, metrics, ledger, jobs, audit, approvals, compliance rules, experiments |
| `ops.py` | audit log with secret redaction, run state (running/paused/stopped), job queue, spend guard, approvals |
| `marketplaces/` | one adapter per marketplace with an explicit capability map |
| `campaigns.py` | ingest, change detection, language detection, scoring (0-100), profitability (4 scenarios), allocation |
| `integrations.py` | Integration Setup Manager: registry, live tests, `INTEGRATION_STATUS.md` |
| `crayo.py` | Crayo API client (from the OpenAPI spec) + cost estimate |
| `production.py` | local engine: ingest, transcription, moment detection, render, fingerprint |
| `qc.py` | automated quality control + caption builder |
| `accounts.py` | account fit + assignment algorithm, posting caps |
| `publishing.py` | YouTube uploader (official API), manual publisher |
| `pipeline.py` | produce → plan → publish → submit → deadlines → rejections → metrics |
| `manager.py` | central manager: job handlers, periodic schedule, worker loop, reports |
| `growth.py` | breakdowns, recommendations, experiments |
| `telegram_bot.py` | control center |
| `dashboard/` | 21-section web dashboard |
| `backup.py` | daily DB backups (14 kept) |

## Money semantics
`Submission.earnings_estimated_usd` is computed from **public** views and is never shown as income.
`eligible_views` / approved / paid figures come only from the marketplace (entered via `/approved`, `/paid`, or a future
API). Cash net profit = ledger `paid` revenue − costs.

## Safety rails
- Pause (no new production/publishing/submission) and emergency stop (cancels queued jobs) via Telegram or dashboard.
- Spend guard: daily/monthly USD limits on variable costs; Crayo capped at 2 test clips until `/approve`.
- Publishing waits for approval when a campaign's rules are only partly visible (login-gated rules).
- Duplicate protection: clip fingerprint, one clip per platform per campaign, URL uniqueness, no resubmission.
- Secrets never logged (`redact()`), `.env` git-ignored, dashboard never renders secrets.
