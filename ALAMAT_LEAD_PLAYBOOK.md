# Alamat Lead Playbook

## Monthly objective

Build a working queue of **100 plausible emerging-VC prospects every month**: 25 per week or about five per working day. At a 3–5% close rate, 100 well-targeted prospects creates a realistic path to 3–5 clients.

The queue is a research and sales-development input, not permission to contact anyone. Every row must remain factual about what is verified, what is only a signal, and what still needs checking.

## Alamat positioning

> Alamat helps newly launched VC firms look credible before their reputation catches up.

Route prospects to one of three offers:

1. **Website:** no site, broken site, placeholder, or materially incomplete site.
2. **SMM and branding:** adequate site but absent, incomplete, or inactive company presence.
3. **Brand positioning:** adequate execution but weak differentiation, proof, or founder/company narrative.

An adequate website is no longer an automatic rejection. It changes the offer. Hasar is the real client proof of professional execution. VentureVive, Arctis Ventures, Swoosh Capital, Stellar VC, and Liquid Ventures are concept projects and must always be labeled as concepts.

## Volume-first funnel

```text
90-day SEC discovery + public launch signals
→ deterministic hard-gate filtering and deduplication
→ 100-prospect monthly queue
→ 20 deep-research rows + 80 light-verification rows
→ LinkedIn or Sales Navigator identity check
→ offer routing from factual presence gaps
→ approval queue
→ controlled outreach
```

The scraper and queue builder do the repetitive work. ChatGPT is reserved for the top 20, ambiguous identity decisions, final pre-contact checks, and replies from prospects.

## Discovery sources

- **SEC Form D:** strongest early signal for a new fund vehicle. Preserve the exact filing type, offering amount, amount sold, first-sale date, related parties, and source URL.
- **Public launch coverage:** accept only defensible first-fund, debut-fund, inaugural-fund, or new-VC-firm headlines. Treat the headline as a lead signal, not proof of the manager identity or website.
- **LinkedIn and Sales Navigator:** use after discovery to identify the actual founder or GP, current role, relationship state, and recent activity. Do not use it as the only freshness source.

## Hard rejects

Exclude a row from the monthly queue only when deterministic evidence shows one of these:

- series, SPV, syndicate, co-investment, continuation, access, feeder, or deal-specific vehicle;
- Fund II or later, or a known established manager launching another vehicle;
- explicit non-VC asset class such as private equity, credit, hedge, or real estate;
- duplicate firm or filing;
- defunct, fraudulent, unrelated, already contacted, or `do_not_contact`;
- a prior audit recorded an equivalent evidence-backed hard rejection.

Do **not** hard-reject a discovery merely because its website is adequate, the Form D is an amendment, its identity is not yet resolved, or its fund size is unknown. Those facts reduce priority or determine the next verification step.

## Monthly priority score

The deterministic `prospect_score` ranks research order. It does not claim final qualification.

- Newness and Fund I evidence: up to 30.
- Capital activity or offering signal: up to 15.
- Decision-maker and exact-profile evidence: up to 20.
- Signal recency: up to 15.
- Alamat presence opportunity: up to 15.
- Source confidence: up to 5.

Tiers:

- `A` — 75–100: first verification priority.
- `B` — 60–74: normal verification queue.
- `C` — below 60: light verification or reserve capacity.

## Research-depth budget

- `complete`: prior factual research already exists; do not pay to repeat it.
- `deep`: maximum 20 unresolved rows per monthly batch. Verify manager history, firm identity, site, LinkedIn, and the exact service opportunity.
- `light`: up to 80 rows. Use the prepared exact-name search links to resolve identity, site, and LinkedIn status without generating a long report.

Deep research is allocated only to rows with a plausible person or firm identity. Pure `identity_lookup` rows remain light until the identity is found.

## Website and presence taxonomy

Use one factual website status:

- `unknown`: not researched.
- `official_domain_verified`: official domain verified; audit incomplete.
- `not_found_after_search`: no official domain after documented firm and founder searches.
- `broken`: official domain repeatedly fails or returns a persistent error.
- `placeholder`: parked, under-construction, or coming-soon.
- `thin_or_incomplete`: missing at least two of named team, investment thesis, portfolio/activity, or working contact path.
- `adequate`: functioning site presents the firm, team, focus/activity, and contact path.

Do not call a site weak merely because its style is plain. For an adequate site, check whether the company LinkedIn page, content cadence, visual identity, positioning, or proof still creates a factual Alamat opportunity.

## Monthly working states

- `identity_lookup`: firm or founder is not yet resolved.
- `linkedin_lookup`: a probable human is known; locate the exact current profile.
- `profile_found`: exact profile URL exists but role evidence is incomplete.
- `profile_verified`: current person, firm, and role are verified.
- `approval_queue`: target is verified and waiting for the user's decision.

The durable sales workflow remains:

```text
discovered → verified → awaiting_approval → approved → contacted
```

Terminal or hold states are `rejected`, `duplicate`, `unresolved`, `do_not_contact`, and pre-existing `contacted`.

## No-contact safeguard

Never save, follow, connect, message, email, or otherwise contact a prospect until the user approves the exact target and exact action. A request for a bare LinkedIn connection means no note or additional action.

Before any outreach, re-check the exact profile, current role, relationship state, duplicate history, and the factual offer route. Never describe Alamat's concept projects as client work.

## Operating rhythm

1. Monday: refresh the prior 90 days of SEC signals and public first-fund coverage.
2. Build or refresh the 100-row monthly queue without LLM research.
3. Work 25 rows per week: five per working day.
4. Resolve light identity and LinkedIn checks first.
5. Spend deep research only on the best 20 or immediately before approved outreach.
6. Record every verified fact and rejection so it is never researched twice.
7. Track verified prospects, approved outreach, replies, meetings, and clients—not raw filing count alone.

## Daily automation

The unattended research job may review no more than five survivors per day. Code first removes hard rejects and candidates already present in the review ledger. Research agents then verify the public firm, decision-maker, exact LinkedIn profile, website, company presence, and offer route. A separate validator rejects incomplete `good_lead` verdicts and any result that records a save, follow, connection, message, email, or other external action.

The daily report must distinguish `good_lead`, `needs_review`, and `reject`. Only `good_lead` means a real verified prospect; raw filings and unverified candidates must never be counted as leads.
