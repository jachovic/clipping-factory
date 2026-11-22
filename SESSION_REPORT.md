# Work report — sessions 1–4 (2026-11-20 → 2026-11-22)

## Session 4 (latest)
- **Setup page** (⚙ Konfiguracja): paste keys → saved to `.env`, applied live, tested; Telegram ID auto-detect;
  11 accounts editable; per-campaign source upload or link.
- **To-do page** (✅ Do zrobienia): download clip, copy platform-specific caption, mark posted / submitted, approvals.
- **Clips delivered to your phone** via Telegram (the video file itself + caption).
- **Per-platform captions** (X 280 chars, YouTube `#shorts` + title) that never drop required campaign tags.
- **Optional AI moment ranking** with Claude (Anthropic key on the setup page), spend-guarded and costed.
- **Windows one-click install** (`windows\INSTALUJ.bat`), desktop shortcut, opens setup when done.
- **Full rehearsal passed**: accounts → upload → automatic clips (QC 100) → 4 publish tasks with correct captions →
  posted → 24 h deadline set → submitted. 30 automated tests.
- Still unverified: Crayo, Telegram, Anthropic live calls (need your keys); the Windows installer on real Windows.

# Sessions 1–3

## Where things stand
- Code: https://github.com/jachovic/clipping-factory (`main`). 25 automated tests, run on every push by GitHub Actions.
- **Real money earned: $0.00.** Nothing has been published or submitted yet; that needs your accounts and keys.
- Campaign intelligence is live and real: **97 campaigns tracked** (Content Rewards + Vyro), **24 HIGH-priority**,
  all of them clipping campaigns your accounts can do.

## What was built
| Area | Status |
|---|---|
| Campaign discovery | **Live.** Content Rewards via public pages + its **sitemap (766 campaign pages, refetched only when changed)**; Vyro public board + platforms |
| Scoring 0–100 + profitability (4 scenarios) | **Live.** Accounts for min-payout thresholds, per-video caps, budget left, language, platforms, competition. **UGC/slideshow campaigns excluded** (we can't film them) |
| Campaign alerts | new / closed / rate changes / low budget |
| Clip production | Local engine **tested**: Whisper transcription → best-moment detection → 1080×1920 with hook + word-highlighted captions. Crayo AutoClip client ready (needs key; 2-clip test cap) |
| Quality control | **tested**: format, audio, caption sync, rights, required hashtags/disclosures, duplicates, rules learned from rejections |
| Accounts, publishing, submissions | built: assignment algorithm, posting caps, duplicate protection, submission deadlines (Clipster 24 h), Telegram confirmations |
| Money tracking | estimated / pending / approved / withdrawable / **paid** kept separate; cost per clip, per approved clip, per 1k eligible views; monthly subscriptions |
| Reports | daily, weekly (Mondays), monthly (1st) |
| Telegram control center | built, owner-only; needs your bot token |
| Dashboard | 21 sections, http://127.0.0.1:8080 |
| Running it | `factory run` (one process, any PC), **Windows installer with autostart**, docker-compose for a server |

## Bugs found and fixed this session
- The discover page shows only a sample of campaigns: coverage went from 34 to 97 via the sitemap, with more each cycle.
- Campaigns not on the sample page would have been wrongly marked "closed". Fixed.
- UGC campaigns ("film yourself") were ranked HIGH. They're now excluded.
- Vyro campaigns couldn't reach HIGH (no platform data). Platforms are now parsed.
- "Arabic Campaign"-style language markers were missed. Now detected.

## Not verified yet (needs your credentials)
Crayo API calls · Telegram live messages · Whop bounties · YouTube upload · the Windows installer (no Windows machine here).

## Current top opportunities (live data, 2026-11-21)
1. Mumford & Sons / Chris Stapleton Live Performance — $2.00 CPM, $12k budget left
2. SEGA – Stranger Than Heaven Act 2 Trailer — $2.00 CPM, $16k left
3. Zach Top at the 60th CMA Awards — $2.00 CPM, $1.5k left
4. Backyard Breaks — $2.00 CPM, $10.6k left
5. Kitsch x Nic Vans — $1.50 CPM, $39.7k left
