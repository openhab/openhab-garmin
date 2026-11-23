# HTTP proxy and fault injector between the Garmin app and openHAB.
#
# Standard library only: the shim is the component that decides whether a test
# scenario sees a healthy or a broken server, so it must not have a dependency
# graph that can drift or fail to resolve on an air-gapped runner.
#
# Build context is tests/ (see compose.yaml), so paths are relative to it.

ARG SHIM_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${SHIM_BASE_IMAGE}

LABEL org.opencontainers.image.title="openhab-garmin E2E shim" \
      org.opencontainers.image.description="Records app requests and injects scripted faults" \
      org.opencontainers.image.licenses="EPL-2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHIM_BIND=0.0.0.0 \
    SHIM_PORT=8080 \
    OPENHAB_UPSTREAM=http://openhab-stable:8080

WORKDIR /app
COPY shim/ /app/shim/

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=5s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/__shim__/health', timeout=3)"

CMD ["python", "-m", "shim.server"]
