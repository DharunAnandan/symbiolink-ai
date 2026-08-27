# SymbioLink AI — Working Prototype

Team Techno Bytes · Sustainability Hackathon 2026 · Theme: Circular Economy

A working demo of the platform described in the project spec: a digital matching
system that connects one MSME's waste with another nearby MSME's raw material need,
across an industrial cluster.

## Quick start

```bash
pip install -r requirements.txt
python3 train_ml_models.py   # trains the 5 ML models once, saves ml_artifacts/*.joblib
python3 app.py
```

Then open **http://localhost:5000** in a browser.

### Database setup (MySQL)

Both development and production run on MySQL now (via the pure-Python `PyMySQL` driver in
`requirements.txt` — no C build tools needed). One-time setup:

```sql
-- in the MySQL shell (mysql -u root -p)
CREATE DATABASE symbiolink_dev;
CREATE DATABASE symbiolink;      -- only needed if you'll also run with FLASK_ENV=production
```

Then copy `.env.example` to `.env` and fill in your real MySQL username/password in
`DEV_DATABASE_URL` (and `DATABASE_URL` if you use production mode). Flask-SQLAlchemy
creates the tables inside the database on first run, but not the database itself —
that's what the `CREATE DATABASE` step above is for. To seed it with the project's
synthetic demo data (12 MSME units, listings, an admin login), run:

```bash
python3 database_migration.py
```

No code anywhere in this project uses SQLite- or MySQL-specific SQL — all database
access goes through SQLAlchemy's ORM (`models.py`), so this was purely a connection
string + driver change. The automated test suite (`pytest`) is the one intentional
exception and still runs against an in-memory SQLite database — see `config.py`'s
`TestingConfig` docstring for why (instant setup, no MySQL server required just to run
tests, fully wiped between runs).

`train_ml_models.py` only needs to be run once (or again after pulling changes to
`ml_training_data.py`) — the trained artifacts are reused on every app start. If you skip
it, the app still runs end to end: every ML call gracefully falls back to the original
heuristic it replaced (see "How the ML layer works" below), it just runs pre-ML until you
do train it.

You can also run each layer standalone to see its raw output in the terminal:

```bash
python3 matching.py       # multi-hop matching engine
python3 pooling.py        # logistics pooling
python3 predictive.py     # predictive layer
python3 trust.py          # trust score ledger
python3 whatsapp_stub.py  # simulated WhatsApp/voice intake
python3 optimization.py   # OR-Tools network-optimized matching plan
python3 iot_sensors.py    # simulated ESP32 bin sensor + auto-listing
python3 geofencing.py     # simulated GPS pickup/delivery verification
python3 carbon_credits.py # carbon credit issuance/marketplace demo
python3 brsr_report.py    # BRSR compliance annexure data for two sample units
python3 photo_classifier.py  # trains + self-tests the photo classifier
```

## What's actually implemented

| File | Layer | What it does |
|---|---|---|
| `data.py` | Access & Listing | Synthetic dataset: 12 MSME units, ~20 listings, reference price table, modeled on documented Muzaffarnagar/Mysore-style cluster patterns |
| `whatsapp_stub.py` | Access & Listing | Parses a plain-text message (as if already transcribed from WhatsApp/voice) into a structured listing; asks a follow-up if a detail is missing |
| `predictive.py` | Predictive | Forecasts each unit's next likely waste listing from its simulated posting history |
| `matching.py` | Multi-Hop Matching | Models the cluster as a directed graph and finds direct matches **and 2–3 hop chains** (A → B → C), ranked by savings, distance, and trust |
| `pooling.py` | Logistics Pooling | Groups nearby low-volume (<100kg) matches into shared transport, and shows the net saving with pooled vs. solo transport cost |
| `trust.py` | Verification & Trust | Rolls up two-way ratings from completed exchanges into a per-unit trust score |
| `app.py` + `templates/dashboard.html` | Output | Flask dashboard tying all six layers together into one view |
| `ml_text.py`, `ml_models.py`, `train_ml_models.py` | AI/ML layer | TF-IDF material matcher, and trained classifier/regressor/ranker models backing the three rows above, plus the order-risk model below (see next section) |

This mirrors the architecture in `SymbioLink_AI_Concept_Note_v2.pdf` exactly (same six
layers, same order).

## New features (this round)

Twelve additions on top of the original six-layer prototype, each following the same
"honest about what's real vs. simulated" approach as the rest of the codebase:

| File | Feature | What it does |
|---|---|---|
| `iot_sensors.py` | IoT bin sensors | Simulates an ESP32 + load cell + ultrasonic sensor per bin; auto-creates a sensor-verified listing when the bin crosses its fill threshold — no typing needed. `/iot` dashboard. |
| `brsr_report.py` | BRSR ESG compliance report | Maps a unit's own completed-order history onto SEBI BRSR Principle 6 (waste/GHG) line items — a presentation layer over the existing audit trail, not new data collection. `/report/brsr`. |
| `photo_classifier.py` | Photo-based material classification | A trained scikit-learn classifier (color/texture features) guesses a material family + rough quantity from an uploaded photo, feeding the same listing auto-fill path as the text parser. Wired into `/listings`. |
| `optimization.py` | Network-optimized matching | Solves for the best non-overlapping SET of accepted matches across the whole cluster at once (Google OR-Tools CP-SAT), instead of ranking chains independently. Shown on `/matches` alongside the existing ranked list. |
| `ml_text.py` (extended) + `ml_multilingual.py` | Hinglish / mixed Hindi-English intake | Romanized-Hindi aliases/keywords + Hinglish training examples (always on); a guarded, documented-but-not-installed-by-default MuRIL upgrade path for Devanagari script. |
| `geofencing.py` | GPS-verified pickup/delivery | Simulates a vehicle's GPS position; auto-advances an order `confirmed → picked_up → delivered` when it enters the pickup/delivery geofence, tagged "GPS-verified" vs. a manual tap. Wired into `/orders`. |
| `carbon_credits.py` | Carbon credit marketplace | Converts a unit's accumulated verified CO2e savings into whole-tCO2e credits units can list and sell. `/carbon-credits`. |
| `_order_risk_score()` in `app.py` (model in `train_ml_models.py`/`ml_models.py`) | Order-risk model | A trained `LogisticRegression` scores each active order's probability of completing vs. falling through (cancelled/disputed), from order size, unit distance, and both sides' trust scores. Shown as a risk badge to the seller/admin on `/orders`, and as an "At-risk orders" panel on `/admin`. |
| `_order_risk_explanation()` in `app.py` | Explainable risk scores | Turns the risk badge from a bare number into a one-line, rule-based reason ("mainly due to the order's value and distance between the two units") built from the same 5 factors the model already computes — not real feature-attribution (no SHAP), but enough for a seller to actually act on it. |
| `orders.orders_needing_reminder()` + `whatsapp_service.send_order_reminder()` | Order-lifecycle reminders | Flags orders sitting >24h in a state where a specific party owes the next action (payment, marking picked up, marking delivered) and nudges them over the existing WhatsApp integration + in-app bell. Triggered on demand from `/admin` (no background scheduler in this app), since today's lifecycle notifications only fire the moment a status *changes*, never while it stays stalled. |
| `market_insights.py` | Market price transparency | An honest reference-price guide (byproduct vs. new-material price per material) paired with real completed-order activity from this platform (trade count, kg traded, total saved) — deliberately not a fake "price trend," since no order in this app carries a negotiated price that could actually vary. `/market-prices`. |
| `matching.network_gap_report()` | Network gap analysis | Flags waste/need listings `build_edges()` found zero match for anywhere in the cluster — exactly who's worth recruiting next to grow real waste diverted, shown on `/admin`. |

See each file's module docstring for the specific honest-scope caveat (what's simulated
hardware vs. real logic, what's trained on synthetic vs. real data, etc.) — the pattern
is the same one the original six layers already use, just applied to the new ones.

## How the ML layer works

Five real, trained models replace what used to be hand-coded heuristics — each one
degrades gracefully to its original heuristic if `ml_artifacts/` hasn't been generated
yet (run `python train_ml_models.py`), so the app never breaks on a fresh clone:

| Model | Replaces | Backing tech | Held-out result |
|---|---|---|---|
| Intake classifier (`whatsapp_stub.py`) | Exact-substring alias dict + keyword list | TF-IDF material matcher + `LogisticRegression` waste/need classifier (now trained on Hinglish phrasing too — see `ml_training_data.py`'s `HINGLISH_*_EXAMPLES`) | 95.6% test accuracy |
| Forecast regressor (`predictive.py`) | Plain moving average | `Ridge` regression over interval/quantity/weekday features | +14.2% MAE improvement vs. the moving-average baseline |
| Match ranker (`matching.py`) | Fixed 60/25/15 weighted score | `LogisticRegression` over saving/distance/trust/similarity/hops | 71.0% test accuracy, 0.725 AUC |
| Photo material classifier (`photo_classifier.py`) | N/A — a text-only intake couldn't previously read a photo at all | `RandomForestClassifier` over color/texture features, trained on procedurally-generated synthetic material-family images (not real photos — see the module docstring) | 100% held-out accuracy *on synthetic images* — a materially weaker claim than "on real photos," stated plainly in the code |
| Order-risk model (`_order_risk_score()` in `app.py`) | N/A — orders had no risk signal at all until after they'd already failed | `LogisticRegression` over order value/quantity, distance between the two units, and both units' trust scores | 88.7% test accuracy, 0.711 AUC |

**Why TF-IDF instead of sentence-transformers embeddings, and Ridge instead of
RandomForest/GradientBoosting** (both were the original plan): `sentence-transformers`
pulls in PyTorch (500MB–2GB+), which timed out mid-install in development and would add
real weight to a repo that judges/teammates need to `pip install` quickly. TF-IDF +
cosine similarity is a standard, genuine vector-space ML technique, already available via
scikit-learn (already a dependency), and needs no model download. Separately, tree
ensembles (RandomForest/GradientBoosting) were tested for the forecaster and consistently
scored *worse* than the plain moving-average baseline on this data — the underlying
relationship is close to linear, which trees handle poorly with a small dataset — so the
forecaster uses `Ridge` instead, which does beat the baseline. Both deviations, and the
debugging that led to them, are documented in the relevant files' module docstrings and
comments (`ml_text.py`, `train_ml_models.py`).

The match ranker doesn't turn the matching engine into a black box: `explain_match()`
in `matching.py` still returns a plain-English reason for every ranked chain, and when
the trained ranker produced the score, that reason names the single learned feature
weighing heaviest (via `ml_models.match_ranker_feature_weights()`) — so the "not a
black box" pitch from the original heuristic version still holds.

## Honest scope notes

- **Prices, transport rates, and CO2e-avoided factors are reference estimates**, not
  live market data or a certified LCA — this matches the "known limitations" section
  of the project spec, and is worth saying out loud in a demo rather than implying
  otherwise.
- **Distances fall back to straight-line (Euclidean) estimates** everywhere in the app.
  The Google Maps Distance Matrix integration (`maps_service.py`) is fully wired into
  the matching/pooling layers already — it activates automatically the moment a
  `GOOGLE_MAPS_API_KEY` is set in `.env` (see `.env.example`), no code changes needed.
  The Matching Engine and Pooling pages label each distance figure "Google Maps" or
  "estimated" so this is never silently misrepresented.
- **The dataset is synthetic**, built to resemble documented cluster patterns, not real
  MSME records — a real pilot would replace `data.py` with actual onboarded units.
- **The ML models are trained on synthetic data** (see `ml_training_data.py`), the
  same honest caveat as the dataset above — a real pilot would retrain all five on
  actual logged messages, listing history, match outcomes, and order outcomes once
  enough accumulate.
  The held-out accuracy/MAE/AUC numbers reported above are real, checkable numbers
  computed against that synthetic data's own held-out split, not invented figures.
- **The chain-finding graph search itself (DFS over 1–3 hop paths) stays rules-based**,
  not learned — only the *ranking* of chains it finds is now ML. `explain_match()` still
  returns a plain-English, inspectable reason for every ranked chain (see "How the ML
  layer works" above), so it's still transparent and easy for a judge (or an MSME owner)
  to trust, not a black box.
- **IoT bin sensors and GPS-verified pickup/delivery simulate the hardware side**
  (`iot_sensors.py` / `geofencing.py`) — there's no physical ESP32/load cell/GPS tracker
  in this demo, only whatsapp_stub.py-style simulated device pushes. The auto-listing and
  auto-status-transition *logic* those pushes trigger is real and already wired through
  the same code paths a real device integration would call.
- **The photo classifier is trained on synthetic images, not real waste photos** — see
  `photo_classifier.py`'s module docstring for exactly what's simulated (procedurally
  generated color/texture per material family) and why a real pretrained vision model
  (ImageNet-style) wouldn't actually help here (wrong domain, and the same PyTorch-size
  dependency problem `ml_text.py` already explains avoiding once).
- **The carbon credit marketplace is simulated, in-platform trading**, not a listing on a
  real registry (Verra, Gold Standard, etc.) — a real deployment would need registry-side
  verification against a recognized methodology before these credits could be sold to an
  actual offset buyer. See `carbon_credits.py`'s module docstring.
- **MuRIL/IndicBERT is a documented, guarded upgrade path, not installed by default** —
  `ml_multilingual.py` only activates if both `USE_MURIL=1` is set *and*
  `transformers`/`torch` are separately installed; the always-on default is
  `ml_text.py`'s extended alias/keyword lists, same "big dependency, opt-in only" treatment
  already applied to `sentence-transformers` elsewhere in this project.

## Known limitations & mitigations

Six specific gaps were raised in review. Two of them ("no quantitative retrain
threshold" and "ring-trading identified but not mitigated") are now real,
tested code, not just prose. The rest are honest positioning answers — stated
here rather than left implicit.

- **No public prototype link.** Fair criticism: every claim above of "real,
  running code" is unverifiable without something a reviewer can actually
  open. Until this is deployed somewhere public, treat the test suite
  (`pytest tests/ -q`, 99 passing) and this README's code references as the
  checkable evidence, and prioritize a live deploy (Render/Railway, free
  tier) or a recorded screen-capture demo before the next submission.
- **Maintenance load across six integrations.** Not all six carry equal
  ongoing cost. `maps_service.py` (Google Maps) is configure-once, essentially
  fire-and-forget. `iot_sensors.py` and `carbon_credits.py` are pure
  application code with no external account to maintain at all. Only
  `whatsapp_service.py` (Twilio phone number renewal, webhook uptime) and
  `payment_service.py` (Razorpay KYC, settlement monitoring) need any
  recurring human attention — and both are the same env-gated,
  simulation-mode-by-default components described in "Honest scope notes," so
  a student team can run the whole platform indefinitely without touching
  either.
- **15–30 founding units may be below critical mass for multi-hop matches.**
  The mitigation is in *how* that cohort gets picked, not just its size:
  target units whose waste streams and input needs are known to interlock
  (a metal shop feeding a foundry, a packaging unit feeding a printer) rather
  than recruiting whoever's nearest. `matching.build_edges()`'s chain-finding
  logic is driven by real material overlap in the graph, not headcount — a
  small, deliberately complementary cohort produces real 2–3 hop chains
  sooner than a larger, randomly recruited one would.
- **No stated quantitative threshold for when a model can meaningfully
  retrain.** This is now an actual enforced function, not just a number in a
  writeup — see `model_promotion.py`'s `is_ready_to_promote()`. It encodes
  the same two-part rule described in "How the ML layer works": at least
  `MIN_REAL_EXAMPLES` (100) real accept/reject or complete/fail decisions
  before a held-out comparison means anything at all, **and** the retrained
  candidate must be measured to beat the existing fallback's held-out score
  by at least `MIN_IMPROVEMENT_MARGIN` before promotion — never promoted on
  example count alone. Covered by `tests/test_model_promotion.py`.
- **Ring-trading and other gaming vectors named but not mitigated.** Now
  addressed with three rule-based checks in `trust.py`, live today, not a
  future item: (1) `TrustLedger.score()` caps how many ratings from any
  single repeat counterparty count toward a unit's score
  (`MAX_COUNTED_RATINGS_PER_COUNTERPARTY`), so one partner can't push the
  number up indefinitely through repetition alone; (2) the "Highly
  recommended" badge (`app.py`'s `_trust_badge_info()`) now also requires a
  minimum number of *distinct* trading counterparties
  (`MIN_DISTINCT_COUNTERPARTIES_FOR_TOP_TIER`), closing the gap where two
  units trading only with each other could look highly recommended with zero
  evidence of working with anyone else; (3) `TrustLedger.concentrated_pairs_report()`
  flags any unit whose completed exchanges are ≥70% with a single
  counterparty for manual admin review, surfaced live on the admin dashboard's
  "Trading concentration flags" section. None of this needs machine learning —
  it's transparent, immediate, and tested (`tests/test_data_access_fallback_and_whatsapp_bugfixes.py`
  covers the underlying data layer; ring-trading logic itself has direct unit
  coverage in `trust.py`'s own test additions). Full graph-based anomaly
  detection across the whole trade network remains the harder, real long-term
  fix, and stays a scoped Phase 2 item on top of these stopgaps, not instead
  of them.
- **Single-cluster/association dependency creates concentration risk.** The
  mitigation is to hedge the bet before committing to a pilot: identify two
  or three candidate clusters or associations up front and run lightweight
  parallel outreach to all of them, then commit fully to whichever responds
  first, instead of staking the entire pilot on one relationship from the
  start. This doesn't require any code — it's a sequencing decision for the
  outreach plan.

## Wiring up real WhatsApp/voice (optional, for beyond the demo)

`whatsapp_stub.py` simulates the message already being plain text. To make it real:

1. **Twilio WhatsApp Sandbox** (free trial credit + 100 free messages) — add a Flask
   route:
   ```python
   @app.route("/whatsapp/webhook", methods=["POST"])
   def whatsapp_webhook():
       from_number = request.form["From"]
       body = request.form["Body"]
       result = parse_message(lookup_unit_by_phone(from_number), body)
       ...
   ```
2. **Voice notes**: Twilio gives you a `MediaUrl` for the audio. Download it and run it
   through a free speech-to-text option (OpenAI Whisper, run locally, or Google
   Speech-to-Text's free tier) to get text, then pass that text into `parse_message()`
   exactly like a typed message.

Neither step is wired up in this prototype since it needs your own Twilio
account/phone number — but the parsing logic it would feed into is already built and
tested in `whatsapp_stub.py`.

## Next steps toward a pilot

1. Replace `data.py` with real listings from one partner cluster association.
2. Wire up the Twilio webhook above so owners can actually text the system.
3. Point `DATABASE_URL` at a managed/hosted MySQL instance (e.g. AWS RDS, PlanetScale,
   Railway) instead of a local server for anything beyond a single machine.
4. Tune `PRICE_TABLE_RS_PER_KG` and transport rates against real local quotes.
5. Flash real ESP32 firmware (load cell + ultrasonic) and point it at
   `iot_sensors.simulate_reading()`'s equivalent server endpoint instead of the
   in-app "Simulate next reading" button.
6. Retrain `photo_classifier.py` on real labelled waste photos the moment enough exist,
   replacing the synthetic training images (see its module docstring).
7. Swap a real vehicle GPS/phone-GPS feed into `geofencing.simulate_gps_tick()`'s
   position source instead of the simulated straight-line trace.
8. Register with a recognized voluntary carbon methodology/registry before listing any
   `carbon_credits.py` credit for sale to a real offset buyer.
