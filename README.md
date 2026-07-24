# VC Finder

Dashboard and pipeline for finding fresh venture capital fund signals from SEC Form D filings.

The current product focus is newest firms and newest funds, not website/email enrichment. The pipeline prioritizes:

- original Form D filings
- Fund I and Fund II signals
- recent filing dates
- recent or not-yet-occurred first-sale dates
- recently formed issuers
- SEC filings marked as Venture Capital Fund

Each run preserves the master CSV and marks newly discovered rows with `is_new_since_last_run=yes`. It also stores `first_seen_at` and `last_seen_at`, so the dashboard can show a focused “New This Run” view instead of making you review the whole list again.

For every fresh candidate, the pipeline also searches older SEC Form D filings back to 2001 using the manager name, related entities and people, phone, and address. It verifies matching filings as pooled investment funds before assigning one of three manager verdicts:

- `likely_new`: Fund I or a recently formed issuer with a completed search and no prior manager match
- `existing_manager`: a strong identity match to an older fund, or an explicit Fund II
- `needs_review`: incomplete searches, weak phone/address-only evidence, series/SPV structures, or ambiguous filings

The dashboard automatically focuses on `likely_new` firms when history-checked results are available. Each row retains the matched identity, explanation, earliest prior filing date, and a direct link to the older SEC filing.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

Open `http://localhost:5001`.

## Pipeline CLI

```bash
python pipeline.py --type vc --days 30 --min-size 0 --output ALL_VC_LEADS.csv
python pipeline.py --type fund2 --days 90 --output ALL_VC_LEADS.csv
```

`--type vc` finds fresh VC fund signals. `--type fund2` narrows to fresh Fund II signals. `--min-size` filters by offering amount when the filing provides a numeric amount.

## Tests

```bash
python -m unittest discover -s tests -v
```
