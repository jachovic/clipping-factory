# CAMPAIGN_RULES

Rules the factory enforces. Per-campaign rules always override these defaults. Learned rules (from rejections) live in
the `compliance_rules` table and are applied by QC automatically.

## Universal (all marketplaces)
1. **Source material:** only footage named in the campaign brief. Every source file needs a rights note.
2. **Organic only:** no bought views, bots, engagement groups or ads (Clipster rule; equally expected elsewhere).
3. **Keep posts up:** don't delete paid posts (Clipster: 30 days after payout; Content Rewards: clips earn ~30 days).
4. **Disclosure:** add the brief's required disclosures/hashtags (QC rejects a clip missing any).
5. **Meaning:** clips start and end on sentence boundaries; never cut a quote to change its meaning; no clips chosen only for provocation.
6. **No duplicates:** one rendition per moment per campaign; the same clip is never posted on two accounts of the same platform for one campaign; a URL is submitted once.
7. **No auto-resubmission** after rejection unless the brief explicitly allows it.

## Marketplace specifics
| Marketplace | Submission window | Views that count | Review | Payout |
|---|---|---|---|---|
| Content Rewards | per brief | approved submissions only; bot-likelihood scored | brand reviews every submission | rounds of 7 days + 3-day hold |
| Clipster | **24 h after posting** | only after submission | per campaign | ~1 week after end, $50 min |
| Vyro | per brief (in app) | "verified views" | brief check after post is public | PayPal / bank / crypto |
| Whop bounties | per bounty | n/a (fixed reward) | poster accepts | per accepted submission |

## QC thresholds
90–100 ready · 80–89 acceptable · <80 regenerate. Mandatory failures (aspect ratio, audio, rights, campaign hashtags/
disclosures, duplicates, inactive campaign, learned rules) reject regardless of score.

## Rejection categories
`duration, content_rules, wrong_source, duplicate, branding, hashtags, disclosure, quality, views_not_organic, late, other`
— record with `/rejected <id> <category> <reason>`. Duration reasons like "under 45s" become machine-checked rules.
