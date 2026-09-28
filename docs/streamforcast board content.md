# StreamForecast Prompt Board — CONTENT SPEC

Every prompt below appears on the page VERBATIM inside a copyable block. Do not rewrite, shorten, or "improve" prompt text. Section intros/labels may be lightly formatted.

---------------------------------------------------------------------
## HEADER

Title (page <title>): StreamForecast Prompt Board
Eyebrow: IDS 706 · Final project · Repository B · Option 3
Headline: A 3-day streamflow forecast for the Eno River, built by three agents and checked by you.
Subhead: Copy each prompt into a fresh conversation in order. The repo and docs/plan.md are the only memory the agents share. The gates in between are yours.

Fact strip (small, data-like):
- Gauge: USGS 02085000 · Eno River at Hillsborough, NC
- Drainage area: 66 sq mi · HUC 03020201
- Gauge location: 36.0711° N, 79.0956° W
- Daily discharge record: 1927 – present
- Weather: Open-Meteo (historical archive + 3-day forecast)
- Intervals: empirical residual quantiles, per horizon

Illustration idea (optional, SVG, decorative but true to the subject): a small hydrograph — an observed line for ~30 days that ends at "today", then a 3-day forecast median with a widening 80% band and a lighter 95% band (a "fan"). Label x-axis ticks "−30 d", "today", "+3 d"; y-axis "cfs". Keep it schematic; don't invent precise numbers.

---------------------------------------------------------------------
## SECTION: The cycle (roles overview)

Intro: Three agents, three human gates. Each agent is a separate, fresh conversation and becomes one graded transcript. The gates are where your judgment shows, so each one leaves a trace in the repo.

Show as a horizontal flow (wraps on mobile), alternating agent / gate:

1. ARCHITECT (agent · transcript: <netid>_architect.txt)
   Job: Turn the brief into docs/plan.md. Verifies the two APIs, asks you questions, writes requirements, risks, acceptance criteria, and a manual smoke test per stage. Writes no code.
   Output: docs/plan.md

2. GATE 1 · Plan review (you)
   Job: Read plan.md end to end. Push back in the Architect chat until it's right. Log your changes in the Decisions table as "Student".
   Output: a plan you'd defend

3. BUILDER (agent · transcript: <netid>_builder.txt)
   Job: Implements plan.md one stage at a time. Restates the plan and flags ambiguities before coding. Runs lint and tests after every stage and shows the output.
   Output: working code, tests, Implementation notes in plan.md

4. GATE 2 · Manual smoke test (you)
   Job: From a clean clone, run the documented commands yourself. Build and run the containers. Record what happened in the README.
   Output: Smoke test section + screenshots

5. TESTER (agent · transcript: <netid>_tester.txt)
   Job: Reviews like a PR it didn't write. Checks every acceptance criterion with evidence, probes edge cases, re-runs setup from scratch. Reports findings first and fixes only what you approve.
   Output: Review findings table in plan.md

6. GATE 3 · Triage, fix, reflect (you)
   Job: Decide which findings to fix and which to decline, and say why. Confirm the reruns are green. Write the README reflection in your own words.
   Output: final README

Callout — "Do we need more agents?":
No. The rubric grades three transcripts, and a fourth agent would blur the line between building and testing. The extra jobs you might hand to a fourth agent already have a home:
- Checking APIs and data before designing → the Architect's first step.
- Writing the README and reflection → you, at Gate 3. The rubric wants your own writing.
- Fixing what the Tester finds → the Tester chat, after you triage. That keeps the whole find → decide → fix → rerun loop in one transcript.

Callout — "What changed from the class demo":
- Names match the assignment: Architect / Builder / Tester (the demo's Architect / Implementer / Reviewer).
- One conversation per role for the whole project, with a follow-up turn per stage. The demo opened 18 chats; you need 3 transcripts, and follow-up turns are where the rubric looks for your review.
- plan.md gains Requirements, Risks, Acceptance criteria with IDs, a Decisions log, Implementation notes, and Review findings.
- The Tester reports before it fixes. In the demo the Reviewer fixed things silently; here you decide.
- Evidence rule: no agent may say "works" without showing the command and its output.

---------------------------------------------------------------------
## SECTION: Pipeline stages (reference)

Intro: The Builder works through these in order. Each stage ends with lint + tests green and a smoke test you can watch.

Stage table (Stage | What it does | Writes to):
0 Skeleton | Package layout, config from env vars, path helpers, Makefile, pytest markers, black + flake8, CI workflow | —
1 Ingest | Pulls USGS daily discharge and Open-Meteo weather (archive, recent days, 3-day forecast), incremental, retries, offline fixture mode | data/raw/
2 Features | Joins by local date, handles USGS qualifiers and gaps, keeps floods, builds lags and antecedent rain | data/features/
3 Train | One model per horizon (t+1, t+2, t+3), time-ordered splits, persistence baseline, residual quantiles saved with the checkpoint | data/models/
4 Forecast | Newest checkpoint + latest flow + weather forecast → 3 days of median, 80% and 95% bounds | data/forecasts/
5 Dashboard | Streamlit: last 60 days + 3-day fan chart, skill vs persistence, interval coverage | reads data/ only
6 Containers | Dockerfile, .dockerignore, Compose with pipeline + dashboard services, shared volume, healthcheck, env config | named volume

---------------------------------------------------------------------
## SECTION: 1 · Architect conversation

Intro: Open a fresh conversation. Paste A1. Answer its questions. Then use the review prompts in A2 as your follow-ups: pick the ones that matter and write your own too. Finish with A3.

### A1 · Kickoff — the brief
(If you installed the dev-cycle skill, start the message with: /dev-cycle architect )

PROMPT A1:
```
You are the ARCHITECT for a new repository. Assume you have no prior conversation with me: the repository and docs/plan.md are the only shared context between you and the agents who come after you. Do not write implementation code. You may create and edit docs/plan.md only.

PROJECT
StreamForecast: an automated pipeline that forecasts daily streamflow 3 days ahead for the Eno River at Hillsborough, NC (USGS gauge 02085000, 66 sq mi, HUC 03020201, gauge at 36.0711 N, -79.0956 W), with uncertainty bands, shown in a dashboard.

Pipeline stages (each a separate module that hands off through files under data/, never by importing another stage's runtime logic):
0. Skeleton: package, config from environment variables, path helpers, Makefile as the public interface, pytest markers (unit / regression / integration), black + flake8, GitHub Actions CI.
1. Ingest: USGS daily values (parameter 00060, discharge in cfs) and Open-Meteo daily weather (precipitation sum, temperature max/min) from both the historical archive and the 3-day forecast endpoint. Incremental fetch. Writes timestamped files to data/raw/.
2. Features: join on local calendar date, handle USGS data qualifiers and gaps, build lagged flow and antecedent-precipitation features. Writes to data/features/.
3. Train: one direct model per horizon (t+1, t+2, t+3), scikit-learn only, no deep learning; you pick the model family and justify it in the Decisions log. Prediction intervals from the empirical distribution of residuals on a held-out calibration period. Publishes a versioned checkpoint plus a metrics sidecar to data/models/.
4. Forecast: loads the newest checkpoint, the latest observed flow, and the weather forecast; writes a 3-day forecast (median, 80% and 95% bounds) to data/forecasts/.
5. Dashboard: Streamlit, reads data/ only.
6. Containers: Dockerfile, .dockerignore, Docker Compose with two services (a scheduled pipeline service and the dashboard) sharing a named volume; healthcheck; configuration by environment variables.

NON-NEGOTIABLES
- Automated tests never touch the network. Commit small recorded API responses under tests/fixtures/ and mock the HTTP layer. CI must pass offline.
- Time series discipline: splits are time-ordered, never shuffled; no feature may use information from after the forecast issue time.
- Every forecast is compared against a persistence baseline (tomorrow's flow = today's flow).
- The Makefile is how humans run things. Smoke tests use make or docker compose commands, not ad-hoc python.
- Keep it the smallest design that meets the goal. No model comparison, no hyperparameter search, no extra services.

WHAT I WANT FROM YOU, IN THIS ORDER
1. Inspect the repository and tell me what's there.
2. If you have network access, make one small real request to each API (USGS daily values for 02085000, last 10 days; Open-Meteo forecast for the gauge coordinates, 3 days, timezone America/New_York). Report the exact field names, units, qualifier codes, and anything surprising. Two things to check specifically: USGS is moving from the legacy waterservices.usgs.gov endpoints to its newer api.waterdata.usgs.gov API, so confirm which one is current and record the choice in the Decisions log; and Open-Meteo's historical archive lags real time by several days, so say how the most recent days get filled (its forecast endpoint's past_days option is one route). If you can't reach the APIs, say so and list what the Builder must verify.
3. Ask me up to 5 clarifying questions in one batch, each with your recommended answer. Wait for my replies before writing the plan.
4. Write docs/plan.md using the structure below.

docs/plan.md STRUCTURE
# StreamForecast — living plan
## Project
- Problem statement (2–3 sentences, who would use this forecast and why)
- Requirements (R1, R2, …)
- Out of scope
- Risks and design concerns (each with likelihood, impact, and mitigation)
- Decisions log (table: ID | Decision | Alternatives considered | Decided by: AI or Student | Why)
- Environment and commands (Python version, how to install, the make targets)
Then one section per stage:
## Stage N — Title
### Goal
### Acceptance criteria (checkboxes with IDs: AC-N.1, AC-N.2, …; each one testable)
### Proposed changes (files to create or edit, with one-line purpose each)
### Architecture / boundaries (what this stage may read, write, and must never import)
### Risks for this stage
### Automated tests (unit / regression / integration; name which AC each test covers)
### Manual Smoke Test
#### What we're proving
#### Terminal (pasteable commands, label Terminal 1 / 2 if needed)
#### Watch for (visible logs, files appearing, readable output)
#### Stop
### Implementation notes (leave empty: the Builder fills this in)
### Review findings (leave empty: the Tester fills this in)

The Manual Smoke Test is a live demonstration a person watches: logs, files on disk, a page in the browser. It is never pytest.

Stop after writing docs/plan.md. End with a short summary: the 5 decisions that most shape the build, and anything still open.
```

### A2 · Review prompts (your follow-ups)
Intro: This is where Architect points are earned. Use several. Edit them in your own voice.

PROMPT A2-a (intervals):
```
Walk me through exactly how the residual-quantile intervals are computed: which period the residuals come from, whether they're in log space or cfs, and how they're applied to a new forecast. How will we know the 80% band actually covers about 80% of outcomes? Add an acceptance criterion for interval coverage on the test period.
```

PROMPT A2-b (forecast weather):
```
The models train on observed weather but at forecast time they get forecast weather, which is itself wrong sometimes. How does that bias the intervals? Is it a problem we fix or a limitation we document? Put your answer in the Risks section either way.
```

PROMPT A2-c (floods are signal):
```
Check the plan's outlier handling. Big flood peaks are the most important thing this forecast exists for, so they must not be clipped or dropped as outliers. Separate "physically impossible" values (negative flow, missing-value sentinels) from "extreme but real" ones, and make that distinction a testable acceptance criterion.
```

PROMPT A2-d (dates and time zones):
```
USGS daily values are local calendar days; Open-Meteo aggregates daily values in whatever timezone we request. Make sure the plan pins both to America/New_York and adds a test that a day of rain lines up with the same day's flow.
```

PROMPT A2-e (stale or missing data):
```
What should Forecast do if USGS hasn't posted yesterday's value yet, if the latest values are provisional, or if Open-Meteo returns fewer than 3 forecast days or a rate-limit error? Decide the behavior for each case and add them as acceptance criteria.
```

PROMPT A2-f (training window):
```
The gauge record goes back to 1927 and Open-Meteo's archive to 1940, but land use in the basin has changed a lot. Recommend a training window and justify it in the Decisions log. I'm leaning toward roughly the last 20 years. Tell me if you disagree.
```

PROMPT A2-g (is Compose justified?):
```
Argue against your own design for a moment: is a two-service Compose setup justified, or would a single container do? Keep whichever is right and record the reasoning in the Decisions log.
```

PROMPT A2-h (apply my edits):
```
I'm making these changes to the plan: [list your changes]. Update docs/plan.md, and record each one in the Decisions log as "Decided by: Student" with my reason. Don't change anything else.
```

### A3 · Close
PROMPT A3:
```
Give me the final plan in 10 bullets or fewer, the list of acceptance criteria IDs by stage, and any open questions. Confirm you haven't written any implementation code.
```

---------------------------------------------------------------------
## GATE 1 · Plan review (you)
Style: checklist, clearly marked "You, not an agent".
- Read docs/plan.md top to bottom once, slowly.
- Every acceptance criterion can be checked by a test or a command. Rewrite the vague ones.
- Floods are kept; impossible values are dropped. The plan says which is which.
- Splits are time-ordered, and the persistence baseline is in the plan.
- Tests are offline, with fixtures named.
- At least one Decisions-log row says "Student". This row becomes your "changed or rejected" example in the README.
- Commit: git add docs/plan.md && git commit -m "Architect plan, reviewed"
- Export the Architect conversation now, before you forget (see Transcripts).

---------------------------------------------------------------------
## SECTION: 2 · Builder conversation

Intro: Open a fresh conversation. Paste B1 once. Then send B2 for each stage from 0 to 6, reviewing between stages with B3 prompts. Finish with B4.

### B1 · Kickoff
(With the skill: /dev-cycle builder Stage 0)

PROMPT B1:
```
You are the BUILDER. Assume the planning conversation is unavailable. Inspect the repository and read docs/plan.md first; it is the contract for what to build, including acceptance criteria and each stage's Manual Smoke Test.

Before writing any code:
1. Summarize the plan back to me in 5 bullets.
2. List anything in the plan that is ambiguous, contradictory, or that you think is wrong. Don't silently resolve it; ask me.

Rules for the whole conversation:
- Build one stage per turn, in order, only when I say which stage.
- Follow existing patterns and keep each change scoped to its stage.
- After each stage, run `make lint` and `make test` and paste the actual output. Never say something works without showing the command and its result.
- Don't weaken or delete a test to make it pass. If a test is wrong, say so and explain why before changing it.
- Tests never touch the network.
- Don't rewrite docs/plan.md. Under the current stage, fill in "Implementation notes" (what you built, any deviations from the plan and why) and tick the acceptance criteria you met. If the Manual Smoke Test commands changed, update only that stage's smoke test so it stays runnable.
- End every turn with: files changed, test results, AC status, deviations, and questions for me.

Start with steps 1 and 2 only.
```

### B2 · Build a stage (send once per stage)
Show a stage picker (0–6) that swaps the stage number and title into this template. Also show, under each stage, a short "Before you move on" list (below).

PROMPT B2 template:
```
Build Stage {N} — {Title} from docs/plan.md.

Scope: only this stage's acceptance criteria. Stop when they are met, lint and tests are green, and the Implementation notes for Stage {N} are filled in. Show me the test output and tell me how to run this stage's Manual Smoke Test.
```

Stage-specific "Before you move on" checks (shown with each stage):

**Stage 0 — Skeleton**
- `make lint` runs `black --check` AND `flake8`; both must pass. (AC-0.1)
- `make test` uses `--strict-markers` and `--disable-socket`; an unmarked test is a collection error, not a skip. (AC-0.2)
- `paths.local_today()` returns `AS_OF_DATE` if set, else `datetime.now(ZoneInfo(TIMEZONE)).date()`. Grep the whole codebase: zero occurrences of `date.today()` or UTC datetime for "today". (AC-0.8)
- `paths.atomic_write()` uses `.tmp` + `os.replace`. Grep: no `.tmp` files match any published file pattern. (AC-0.9)
- Every line in `requirements.txt` and `requirements-dev.txt` is an exact `name==version` pin — no `>=`, `~=`, or bare names. (AC-0.10)
- CI workflow file exists and runs `make lint` and `make test` in that order. (AC-0.6)

**Stage 1 — Ingest**
- `INGEST_SOURCE=fixtures AS_OF_DATE=2026-09-23 make ingest` produces all 5 raw file types with zero network calls; fixture mode without `AS_OF_DATE` exits 1. (AC-1.7)
- A second run (real or fixture) fetches only days after the last file's date — not the full backfill again. (AC-1.2)
- Previous-runs hourly precipitation values are summed per America/New_York calendar date; an incomplete day (no midnight) produces NaN for that date with a WARNING, not a crash. (AC-1.9)
- Every Open-Meteo request includes `timezone=America/New_York`; if the response `timezone` field differs, ingest exits with ERROR and writes no file. (AC-1.10)
- MRMS fetch failure logs a WARNING but never raises the ingest exit code above 0. (AC-1.11)
- USGS fetch follows `next` pagination links; response validation checks units, length, and date coverage. (AC-1.12)

**Stage 2 — Features**
- The Chantal flood peak (8,180 cfs on 2025-07-07) appears in `data/features/` with exactly that value — no clipping, no log transform stored in place of cfs. (AC-2.3)
- Rain aligns with flow on the same local date: 2025-07-06 shows ~54.7 mm and ~1990 cfs. If rain is one day late, timezone pinning is broken. (AC-2.5)
- Lead-matched rain: every row from 2024-02-01 onward has `weather_lead_matched = True` and uses previous-runs API values for `precip_f2`/`precip_f3`; the switch date is exact. (AC-2.8)
- `data/features/rain_check.json` exists with bias, MAE, hits, misses, and false-alarms vs MRMS; confirm 2026-09-18 appears as a lead-0 false alarm. (AC-2.9)
- Feature availability matrix test passes: training rows use historical-forecast rain; calibration/test rows use lead-matched. (AC-2.10)
- No leakage: no feature for row d0 uses any value after d0. (AC-2.6)

**Stage 3 — Train**
- Training aborts (exit 1, clear message) if any calibration or test row has `weather_lead_matched = False`. (AC-3.1)
- Exactly 3 checkpoint files (one per horizon) plus one sidecar JSON written with `allow_nan=False`; checkpoint and sidecar share the same `<ts>` timestamp; both are written atomically. (AC-3.4)
- Residual quantiles come from the calibration period only and are non-decreasing (lo95 ≤ lo80 ≤ hi80 ≤ hi95 at every percentile). (AC-3.3)
- `make coverage` checks that calibration coverage is within ±0.02 of nominal (80 % band in [0.72, 0.88]; 95 % band in [0.90, 0.99]). A real-data failure is a documented finding (D23), not a CI break. (AC-3.6, AC-3.9)
- A horizon where `skill_mae ≤ 0` (worse than persistence) logs a WARNING but still publishes; it must not abort training. (AC-3.8)
- Wet/dry split coverage is computed using MRMS labels after prediction — MRMS is never a model input. (AC-3.10)

**Stage 4 — Forecast**
- `DATA_DIR=/tmp/sf-empty make forecast` prints "no checkpoint yet; skipping forecast" and exits 0. (AC-4.4)
- `stale_days` 1–2: forecast is written with a WARNING. `stale_days > 2`: refused, exits 1, previous forecast file byte-identical (no new content written). (AC-4.5)
- `fc_fetched_date` must equal `local_today()`; stale forecast weather → refused, exits 1. (AC-4.10)
- Fewer than 3 available forecast days → refused, exits 1. (AC-4.7)
- Every refusal leaves the previous `data/forecasts/` file untouched; the file written on the last good run is byte-identical after any refusal. (AC-4.11)
- Every output row satisfies `lo95 ≤ lo80 ≤ median ≤ hi80 ≤ hi95` and all values are ≥ 0 cfs. (AC-4.2, AC-4.3)

**Stage 5 — Dashboard**
- KPIs use the sidecar whose `model_version` matches the displayed forecast's `model_version`, not the newest sidecar on disk. (AC-5.1)
- Forecast points with `valid_date < local_today()` are drawn greyed out and labelled "past". (AC-5.5)
- Chart title states a finding ("Flow expected to rise to about X cfs by Friday"), not a variable name or stage label. (AC-5.3)
- No more than 3 KPI tiles visible at once. (AC-5.4)
- `DATA_DIR` pointed at an empty directory: dashboard shows "No forecast yet", no traceback. (AC-5.6)

**Stage 6 — Containers**
- `docker compose exec dashboard id -u` prints `1000` (not `0`). (AC-6.4)
- `docker compose ps` shows the dashboard service as `(healthy)`. (AC-6.5)
- `docker compose run --rm pipeline make test` exits 0. (AC-6.8)
- `make pipeline` (`--once`) exits with Forecast's exit code; if Forecast refuses, the whole pipeline reports failure even if earlier stages succeeded. (AC-6.10)
- `docker compose down && docker compose up` — forecast files are still present; data persists on the named volume. (AC-6.7)
- **If the agent's environment has no Docker:** it must explicitly mark AC-6.4 through AC-6.8 "not verified" rather than guessing or omitting them. You verify these at Gate 2.

### B3 · Review prompts (your follow-ups between stages)
Intro: Use these after each stage to dig in. The AC IDs below are real — substitute the number from the stage you just built.

PROMPT B3-a:
```
Show me the specific test that proves AC-{N}.{x}, and explain what would make it fail. Walk through the assertion: if the code had the bug the AC guards against, which line in the test would raise?
```

PROMPT B3-b:
```
This doesn't match docs/plan.md: [what you noticed]. Either fix the code to match the plan, or explain why the plan is wrong. If the plan is wrong, don't edit it. Tell me, and I'll take it back to the plan.
```

PROMPT B3-c:
```
Explain why you chose [library / approach] here instead of [alternative]. Keep it to the trade-off that matters for this project.
```

PROMPT B3-d:
```
This is more complicated than it needs to be: [file or function]. Simplify it without changing behavior, then rerun lint and tests.
```

Targeted follow-ups (use the one that fits the stage you just reviewed):

PROMPT B3-e (Stage 0 — time helper):
```
Show me the test for `paths.local_today()`. Verify it respects `AS_OF_DATE` and falls back to the local New York timezone, never UTC. Then grep the whole codebase for `date.today()` and show me the output.
```

PROMPT B3-f (Stage 1 — fixture mode):
```
Run `INGEST_SOURCE=fixtures AS_OF_DATE=2026-09-23 make ingest` with network disabled. Paste the output. Then remove `AS_OF_DATE` and run again — confirm it exits 1 with an error about the missing variable.
```

PROMPT B3-g (Stage 2 — lead-matched rain):
```
Show me a feature-table row from 2024-02-01 and one from 2023-12-31. Confirm `weather_lead_matched` is True for the 2024 row and False for the 2023 row. Show the code that sets this flag.
```

PROMPT B3-h (Stage 3 — interval ordering):
```
After training, run a quick sanity check: for 100 random rows from the calibration set, assert `lo95 ≤ lo80 ≤ hi80 ≤ hi95` at every row. Paste the assertion and the result.
```

PROMPT B3-i (Stage 4 — refusal safety):
```
Simulate a stale-USGS refusal: set `AS_OF_DATE` to a date 3 days after the last raw file, run `make forecast`, and show me the exit code and the diff of `data/forecasts/` before and after (should be identical).
```

PROMPT B3-j (Stage 5 — version matching):
```
Show me the code that selects the sidecar to display KPIs. If I have two sidecars on disk with different `model_version` values and the forecast was produced with the older one, which sidecar does the dashboard use?
```

### B4 · Close
PROMPT B4:
```
We're done building. Give me a final summary: what each stage does, every deviation from docs/plan.md and why, the final `make lint` and `make test` output, and anything you think the Tester should look at closely.
```

---------------------------------------------------------------------
## GATE 2 · Manual smoke test (you)
Style: checklist + terminal block, "You, not an agent". The rubric requires you to do this yourself before the Tester starts.

Terminal block (display, copyable):
```
git clone <your-repo-url> smoke && cd smoke
make install
make test
docker compose build
docker compose up
# second terminal:
docker compose ps
docker compose logs pipeline --tail 30
# browser: http://localhost:8501
docker compose down
docker compose up -d   # confirm the forecast is still there
docker compose down
```
Checklist:
- Setup instructions in the README work exactly as written, from a clean clone.
- Pipeline logs show ingest → features → train → forecast.
- Files appear under the data volume (raw, features, models, forecasts).
- Dashboard loads and shows the 3-day fan chart.
- Dashboard container reports healthy in `docker compose ps`.
- Data survives a down/up cycle.
- Write the result in README → "Manual smoke test": what you ran, what you saw, what broke. Add 2–3 resized screenshots.
- Export the Builder conversation.

---------------------------------------------------------------------
## SECTION: 3 · Tester conversation

Intro: Open a fresh conversation. The Tester is useful only if it's independent, so don't paste the Builder's summary. Send T1–T3, then triage with T4, then close with T5.

### T1 · Kickoff — contract check
(With the skill: /dev-cycle tester)

PROMPT T1:
```
You are the TESTER. Another agent built this project, and you didn't see that conversation. Treat it like a pull request you're reviewing: don't trust summaries, comments, or ticked checkboxes. Trust only what you can run or read.

Read docs/plan.md first. Then build a coverage table with one row per acceptance criterion: AC ID | Status (Met / Partial / Missing / Can't verify) | Evidence (test name, file and line, or command plus output).

Run `make lint` and `make test` and paste the output. Point out tests that would still pass if the code were wrong: tests that assert nothing meaningful, mock away the thing under test, or only check the happy path.

Don't fix anything yet. Report only.
```

### T2 · Edge cases
PROMPT T2:
```
Now try to break it. For each case, write or run a test (use fixtures, no network) and report what actually happens:
- USGS returns an empty series, or only provisional ("P") values, or an ice-affected day
- a gap of 1 day vs a gap of 10 days in the flow record
- a zero or negative flow value
- a flood value larger than anything in the training data
- Open-Meteo returns fewer than 3 forecast days, or a rate-limit error body
- rain and flow on the same local date when the request timezone is wrong
- no checkpoint in data/models/, or a corrupt one
- the latest observed flow is several days stale
- interval ordering on every forecast row (lo95 ≤ lo80 ≤ median ≤ hi80 ≤ hi95, never negative)
- a data leak: any feature computed with information after the issue date
Keep new tests you think are worth keeping. Still don't fix implementation code.
```

### T3 · Setup and containers from scratch
PROMPT T3:
```
Verify the setup instructions as a new user would. Follow the README exactly, in a fresh virtual environment, and report every step that fails or needs something the README doesn't mention. Then build the image and run `docker compose up`. Check the healthcheck, the volume, the environment variables, that the container runs as non-root, and that tests run inside the container. Check .dockerignore keeps data, venvs, and caches out of the image.

Finish with a findings table: ID | Severity (Blocker / Major / Minor / Nit) | What's wrong | Evidence | Recommended fix. Append the same table under "Review findings" in docs/plan.md for the relevant stages.
```

### T4 · Your triage (fill in, then send)
PROMPT T4:
```
Here's my triage of your findings:
- Fix: [IDs], because [reason]
- Don't fix: [IDs], because [reason, e.g. out of scope, or I disagree because ...]
- Fix differently: [ID], I want [your approach] instead of your recommendation because [reason]

Apply only the approved fixes. Fix the implementation, not the tests, unless a test is wrong. After fixing, rerun `make lint`, `make test`, and `docker compose build`, paste the output, and update the Review findings table with the outcome of each finding.
```

### T5 · Close
PROMPT T5:
```
Final verification: rerun the full suite and the container build, confirm every Blocker and Major finding is resolved or explicitly declined, and give me a final status per acceptance criterion. List anything you'd still worry about if this went to production.
```

---------------------------------------------------------------------
## GATE 3 · Triage, fix, reflect (you)
Checklist:
- Every Blocker/Major finding is fixed or has a written reason in the Review findings table.
- CI badge is green on main.
- README covers: option selected (3), purpose, install/run/test, container workflow, smoke-test result, what each role contributed.
- One AI recommendation you accepted, with why.
- One you changed or rejected, with why. The "Student" rows in the Decisions log and your T4 triage are ready-made examples.
- How you independently verified the result: your smoke test, the offline CI run, coverage on the test period.
- Written in your own voice. Edit anything that sounds like an agent.
- Export the Tester conversation.

---------------------------------------------------------------------
## SECTION: Transcripts

Intro: Three plain-text files, unabridged, committed to docs/transcripts/ and uploaded to Canvas separately. No ZIP, PDF, or combined file.
Tip: Export or copy the whole conversation as soon as each role ends (in Claude Code, `/export`; in the Claude app, copy the conversation text). Then add the header and footer and label the speakers. You may redact secrets as [REDACTED]; nothing else gets cut.

Filenames: <netid>_architect.txt · <netid>_builder.txt · <netid>_tester.txt

Header template (copyable):
```
STUDENT_NAME: Grace Inman
NETID: <netid>
OPTION: 3
ROLE: architect
AGENT_TOOL: Claude Code
REPOSITORY_B_URL: https://github.com/<user>/IDS706-StreamForecast
===== TRANSCRIPT START =====
[STUDENT]
...

[AGENT]
...
===== TRANSCRIPT END =====
```

---------------------------------------------------------------------
## SECTION: Take the workflow with you

Intro: The same three roles as a reusable Claude skill.
- Project-level: save the skill as .claude/skills/dev-cycle/SKILL.md in the repo (it's committed, so the grader sees it).
- Or add it to your Claude skills so it follows you to any repo.
Usage (copyable):
```
/dev-cycle architect <feature or project brief>
/dev-cycle builder Stage 2
/dev-cycle tester
```
What the skill adds over the class version: human gates it won't skip, the evidence rule, acceptance criteria with IDs, a Decisions log that records who decided, and a Tester that reports before it fixes.

Footer: Built for IDS 706 · Duke University. Gauge data: USGS Water Data for the Nation. Weather: Open-Meteo.
