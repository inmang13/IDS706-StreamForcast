# StreamForecast: one image for both Compose services (pipeline, dashboard).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# make: the Makefile is the interface inside the container too (make test).
RUN apt-get update \
    && apt-get install -y --no-install-recommends make \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

WORKDIR /app

# Dependencies first, in their own layer, so code changes don't reinstall them.
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

# Code, installed editable so fixture mode finds /app/tests/fixtures (D17).
COPY . .
RUN pip install --no-deps -e . \
    && mkdir -p /data \
    && chown -R app:app /app /data

ENV DATA_DIR=/data \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

USER app
# Declared after the chown so a fresh named volume is seeded owned by app (RK14).
VOLUME /data
EXPOSE 8501

CMD ["python", "-m", "streamforecast.scheduler"]
