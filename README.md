# StreamForecast

Automated daily 3-day streamflow forecast, with 80% and 95% uncertainty bands and a
persistence baseline, for the Eno River at Hillsborough, NC (USGS 02085000).
The design contract is [docs/plan.md](docs/plan.md).

## Install

Python 3.12 or newer, GNU Make, and a POSIX shell (Git Bash on Windows).

```bash
git clone https://github.com/inmang13/IDS706-StreamForcast.git
cd IDS706-StreamForcast
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows; .venv/bin/activate on Linux/macOS
# PowerShell: .\.venv\Scripts\Activate.ps1
make install
```

## Run

```bash
make help      # every target
make config    # resolved configuration; override any setting with an env var
DATA_DIR=/tmp/sf make config
```

Configuration comes only from environment variables; `.env.example` lists each one
with its default.

## Test

```bash
make lint      # black --check + flake8
make test      # offline: pytest-socket blocks any network access
```

Every test carries exactly one marker: `unit`, `regression`, or `integration`.

## Pipeline

```bash
make pipeline   # one pass: ingest -> features -> train (if needed) -> forecast
make dashboard  # http://localhost:8501
```

`make pipeline` exits with Forecast's exit code: 0 = forecast written (or no model
yet), 1 = Forecast refused (stale data or weather) or failed. A failed ingest source
does not fail the pass by itself.

## Containers

One image, two Compose services sharing the named volume `sfdata` (mounted at `/data`):

- `pipeline`: `python -m streamforecast.scheduler`, a pass on start and then every
  `RUN_INTERVAL_HOURS` (default 24); retrains when the newest model is older than
  `RETRAIN_DAYS` (default 7).
- `dashboard`: Streamlit on http://localhost:8501, with a healthcheck.

```bash
docker compose build
docker compose up -d          # the first start backfills data and trains (a few minutes)
docker compose ps             # dashboard shows (healthy)
docker compose logs -f pipeline
docker compose run --rm pipeline make test
docker compose down           # keeps the data volume; add -v to delete it
```

Both containers run as the non-root user `app` (uid 1000). Every setting in
`.env.example` can be overridden from the shell or a `.env` file next to
`docker-compose.yml`.
