# StreamForecast: the Makefile is the public interface (docs/plan.md).
# Runs inside an activated venv from Git Bash, PowerShell, or any POSIX shell.
# Recipes use only $(PYTHON) and docker (no awk/find/rm), so they need no Unix tools.

PYTHON ?= python
DASHBOARD_PORT ?= 8501
PYTEST_MARKERS = unit or regression or integration

.DEFAULT_GOAL := help
.PHONY: help install config format lint test test-unit test-regression \
    test-integration ingest features train forecast coverage pipeline \
    dashboard docker-build up down logs clean

help: ## List targets with one-line descriptions
	@$(PYTHON) -c "import re; [print(f'  {t:<18} {d}') for t, d in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)', open('Makefile').read(), re.M)]"

install: ## Install runtime + dev dependencies and the package (editable)
	$(PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt -e .

config: ## Print the resolved configuration (env overrides included)
	@$(PYTHON) -m streamforecast config

format: ## Format code with black
	$(PYTHON) -m black .

lint: ## black --check and flake8
	$(PYTHON) -m black --check .
	$(PYTHON) -m flake8

test: ## All offline tests (sockets disabled)
	$(PYTHON) -m pytest -m "$(PYTEST_MARKERS)"

test-unit: ## Unit tests only
	$(PYTHON) -m pytest -m unit

test-regression: ## Regression tests only
	$(PYTHON) -m pytest -m regression

test-integration: ## Integration tests only
	$(PYTHON) -m pytest -m integration

ingest: ## Stage 1: fetch raw data into DATA_DIR/raw
	$(PYTHON) -m streamforecast.ingest

features: ## Stage 2: build the feature table
	$(PYTHON) -m streamforecast.features

train: ## Stage 3: fit models, publish checkpoint + metrics
	$(PYTHON) -m streamforecast.train

forecast: ## Stage 4: write the 3-day forecast
	$(PYTHON) -m streamforecast.forecast

coverage: ## Print test-split interval coverage; non-zero exit if outside tolerances
	$(PYTHON) -m streamforecast.train --report

pipeline: ## One scheduler pass: ingest -> features -> train (if needed) -> forecast
	$(PYTHON) -m streamforecast.scheduler --once

dashboard: ## Run the Streamlit dashboard on DASHBOARD_PORT
	$(PYTHON) -m streamlit run streamforecast/dashboard.py --server.port $(DASHBOARD_PORT)

docker-build: ## Build the Compose image
	docker compose build

up: ## Start both Compose services in the background
	docker compose up -d

down: ## Stop the Compose services (keeps the data volume)
	docker compose down

logs: ## Follow Compose logs
	docker compose logs -f

clean: ## Remove caches (never touches data/)
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__') if '.venv' not in p.parts]; shutil.rmtree('.pytest_cache', ignore_errors=True)"
