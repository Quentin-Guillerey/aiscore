# AISCORE service

[![CI](https://github.com/Quentin-Guillerey/AISCORE/actions/workflows/ci.yml/badge.svg)](https://github.com/Quentin-Guillerey/AISCORE/actions/workflows/ci.yml)

FastAPI service for scoring contact-centre QA evaluations. Postgres for the
normal deployment, SQLite for the standalone build. Prototype stage:
mock and manually entered data only.

The scorecard has two independent axes, and keeping them separate is the
core design decision:

- **Weighted criteria** roll up into a base score. 14 criteria, weights
  summing to 1.0, channel-scoped so voice and chat evaluate different subsets.
- **Compliance violations** are evaluated once per interaction and override
  the base score entirely rather than blending into it. A violation is not a
  heavier criterion, it is a different kind of judgement.

## Verification status

**Verified by CI on every push:** 55 tests across scoring logic, scorecard
data integrity, and the HTTP layer.

- **Scoring engine, 15 tests.** NA exclusion from both numerator and
  denominator, the partial-credit gate, both override types, override-type
  validation, and input validation.
- **HTTP layer, 33 tests.** Endpoints exercised end to end against an
  in-memory SQLite database via TestClient: request and response shapes,
  session lifecycle, the review endpoint's state transitions, error codes,
  and schema creation on startup.
- **Scorecard data, 7 tests.** Weights sum to 1.0, no duplicate IDs, every
  criterion has valid channels and a populated automation method, every
  compliance category resolves to exactly one override type.

CI also fails on any warning, verifies every test file is actually collected
by pytest, enforces a minimum test count, and builds the Docker image.

**Verified manually, not by CI.** The service has been run end to end under
`docker compose` against Postgres and exercised through the interactive docs:
a clean interaction scored, a compliance flag held at `pending_human_review`,
a demotion attempt rejected with 422, a flag overturned on review with the
score reverting and the flag preserved, and a second review on a settled score
refused with 409. The PyInstaller executable has also been built on Windows
and starts against SQLite.

**Not covered by either, and therefore unproven:**

- CI never starts uvicorn or serves a request over a real socket. The HTTP
  layer is exercised through TestClient, which calls the app directly.
- CI never runs against Postgres. Its database layer runs on SQLite only, so
  Postgres-specific behaviour under CI (native UUID type, connection pooling)
  is covered only by the manual run above, not on every push.
- No load, concurrency, or performance testing of any kind.
- Nothing has been run against real interaction data. See the last section.

## Scorecard data

`data/criteria.csv` and `data/rcm_violations.csv` are original content written
for a generic contact centre. 14 weighted criteria and 37 violations across 9
categories, with no employer, client, product, or system named anywhere, and
no thresholds, dollar amounts, or timeframes carried over from any real
policy. Where a rule depends on a number, the text states the rule and leaves
the number to configuration.

The structure, two independent axes with derived override types, is modelled
on how mature contact-centre scorecards are built. The content is not lifted
from one.

Editing either file is safe: `tests/test_scorecard_data.py` fails the build on
weights that do not sum to 1.0, duplicate IDs, invalid channels, an
unpopulated automation method, or a category resolving to more than one
override type.

## Where override_type comes from

`override_type` is derived from `data/rcm_violations.csv`, keyed off the
selected category. Callers do not choose it.

This is deliberate. A common pattern in spreadsheet-based scorecards is to
make the override outcome an independently selected field with no enforced
link to the chosen category, so an evaluator can flag one thing and apply the
outcome of another. Deriving it removes that class of error entirely.

One exception: a category whose resolved type is `partial_fail` may be
promoted to `auto_zero` under the goodwill rule. Promotion only, never
demotion, and only for categories on an explicit allow-list. A demotion
attempt is rejected with 422. This matters because demotion would not just
produce a wrong score, it would also move the result out of
`pending_human_review` and skip the gate described below.

## The human-in-the-loop gate

`auto_zero` results are computed but held as `pending_human_review` until a
person confirms or rejects them. `partial_fail` results publish immediately.

The line is drawn on recoverability, not severity. A `partial_fail` caps the
score and leaves the criterion-level detail intact, so an error is
correctable through normal audit. An `auto_zero` discards the base score
entirely and there is nothing left to inspect. Full reasoning, including why
PCI compliance sits outside the gate, is in
`RCM_human_in_the_loop_policy.md`.

On rejection, `final_score` reverts to `base_score` and `override_superseded`
is set. **The flag itself stays on the record.** Which categories get
overturned, and how often, is the best available signal that a category or
its violation definitions need work, and clearing the flag would delete the
only record it was ever raised.

## Run it (Docker + Postgres)

```
cp .env.example .env
# edit .env, set a real POSTGRES_PASSWORD
docker compose up --build
```

API comes up on `http://localhost:8000`. Interactive docs at `/docs`.

## Try it

Create a clean interaction. Ratings must cover every criterion applicable to
the channel; a missing one is a 422, not a silent zero. See
`data/criteria.csv` for the IDs.

```
curl -X POST localhost:8000/scores -H 'Content-Type: application/json' -d '{
  "interaction_id": "demo-1",
  "channel": "chat",
  "ratings": { "1.1": "meets_expectations", ... }
}'
```

Trigger the human-in-the-loop gate:

```
curl -X POST localhost:8000/scores -H 'Content-Type: application/json' -d '{
  "interaction_id": "demo-2",
  "channel": "chat",
  "ratings": { "1.1": "meets_expectations", ... },
  "rcm_flag": { "category": "Fraud", "violation_id": "V1" }
}'
```

The response carries `"status": "pending_human_review"` and
`"final_score": 0.0`, computed but not yet authoritative. Pull the reviewer
queue:

```
curl localhost:8000/scores?status=pending_human_review
```

Confirm or reject:

```
curl -X POST localhost:8000/scores/{id}/review -H 'Content-Type: application/json' -d '{
  "decision": "confirm",
  "reviewed_by": "qa_manager"
}'
```

## Standalone build (no Docker or Postgres)

For a machine without Docker, or to hand someone a single portable file,
`run_standalone.py` starts the API against a local SQLite database. On
Windows:

```
pip install -r requirements-standalone.txt
python -m PyInstaller --onefile --name aiscore --add-data "data;data" run_standalone.py
dist\aiscore.exe
```

`build_exe.bat` runs those two steps. The result is a single `aiscore.exe`
needing no Python, Docker, or Postgres on the target machine. It creates
`aiscore_standalone.db` next to the executable. Demos and single-user
testing only.

Implementation notes, since this path diverges from the main one:

- `run_standalone.py` sets `DATABASE_URL` before importing `app.main`. That
  ordering is load-bearing: `app.db` reads the variable at import time, so
  moving the import above the assignment breaks startup.
- `app/db.py` has no default `DATABASE_URL` on purpose. On the Postgres
  path a missing value should stop the service rather than silently write to
  a local file nobody knows about.
- `app/models.py` uses a portable `GUID` type mapping to Postgres' native
  UUID where available and `CHAR(36)` on SQLite.

The SQLite code path is exercised by the test suite, and the executable has
been built and run on Windows 10. It is not built by CI, so a change that
breaks the PyInstaller bundling would not be caught automatically.

## Not enforced by this prototype

Stated explicitly so the gaps are not inferred from silence.

- **No authentication on any endpoint.** `reviewed_by` is a free-text string
  the caller asserts.
- **No separation of reviewer from evaluator.** The intended control is that
  an `auto_zero` flag is reviewed by someone other than the person who raised
  it. Nothing implements this, and enforcing it needs an identity layer and
  an evaluator field on the score row.
- **Review fields are mutable columns, not append-only events.** A completed
  review cannot be overwritten through the API, which returns 409, but
  nothing at the storage layer prevents direct modification.

## Before this touches real interactions

None of this is done, and all of it is required first:

- Alembic migrations instead of `create_all`
- Redaction before storage and before any hosted-API call
- Retention windows for transcripts and audio
- Calibration against human-scored interactions, reporting per-criterion
  agreement using Cohen's kappa rather than raw accuracy, since the rating
  classes are heavily imbalanced. Criteria below threshold get disabled, not
  shipped with a caveat.
- A real appeals path beyond confirm/reject
- Audit logging beyond the `reviewed_by` and `reviewed_at` columns
- Authentication, and the reviewer separation described above

Scores are advisory until that list is closed.

## Mock data is a pipeline test, not an accuracy measurement

Self-recorded and manually entered data is good for end-to-end wiring, schema
correctness, and catching regressions. It cannot tell you whether the scoring
is accurate. Scripted material is unrealistically clean, self-labelled data
has no inter-rater signal, and thresholds fitted to it will not transfer.
Nothing here should be tuned against it beyond getting the plumbing right.
