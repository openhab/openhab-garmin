# Ubuntu 24.04 + Wine + Xvfb + xdotool + ImageMagick + Python 3.
#
# Everything the harness needs to compile the PRG with the Linux `monkeyc`
# (inside the mounted SDK) and to drive the Windows Connect IQ simulator under
# Wine. The SDK itself is never copied in: it is mounted at /opt/ciq-sdk and
# validated at container start (see entrypoint.sh).
#
# Build context is tests/ (see compose.yaml).

ARG RUNNER_BASE_IMAGE=ubuntu:24.04
FROM ${RUNNER_BASE_IMAGE}

LABEL org.opencontainers.image.title="openhab-garmin E2E runner" \
      org.opencontainers.image.description="Builds the PRG and drives the Connect IQ simulator under Wine" \
      org.opencontainers.image.licenses="EPL-2.0"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# wine32:i386 is required: the simulator is a 32-bit Windows binary, and a
# wine64-only install fails at launch with an opaque loader error.
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        python3 \
        python3-venv \
        xvfb \
        x11-utils \
        xauth \
        xdotool \
        imagemagick \
        unzip \
        zip \
        wine \
        wine64 \
        wine32:i386 \
        libgl1 \
        libglu1-mesa \
        libx11-6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 marks the system Python as externally managed; a venv keeps the
# harness dependencies out of the distro packages.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY runner/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY runner/entrypoint.sh /entrypoint.sh
# Normalise line endings: a CRLF shebang turns into an unhelpful
# "no such file or directory" at container start.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# The repository is bind-mounted at /work by compose; these paths only need to
# be correct for the CLI to import itself and to find the shared shim grammar.
ENV PYTHONPATH=/work/tests/runner:/work/tests \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    E2E_ARTIFACTS=/work/artifacts \
    DISPLAY=:99

WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
