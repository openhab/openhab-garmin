# Ubuntu 24.04 + Wine + Xvfb + xdotool + ImageMagick + Python 3.
#
# Everything the harness needs to compile the PRG with the Linux `monkeyc`
# (inside the mounted SDK) and to drive the Windows Connect IQ simulator under
# Wine. The SDK itself is never copied in at build time: it is mounted
# read-only at /opt/ciq-sdk, copied to a writable path at container start (see
# entrypoint.sh) and validated there.
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
#
# default-jre-headless is required by `linux/bin/monkeyc`, which is a shell
# wrapper around monkeybrains.jar; without a JRE the image cannot build a PRG.
#
# libharfbuzz0b, libfreetype6, libfontconfig1 and fontconfig are the shared
# libraries `libfontmanager.so` links against. monkeyc generates resources, so
# it loads AWT's font manager even on a headless JRE; without them every compile
# dies with `UnsatisfiedLinkError: libharfbuzz.so.0: cannot open shared object
# file`. openssl generates the ephemeral developer key (`e2e build`), so it is
# installed explicitly instead of relying on a transitive dependency.
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        default-jre-headless \
        fontconfig \
        libfontconfig1 \
        libfreetype6 \
        libharfbuzz0b \
        openssl \
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

# Smoke check for the font stack. A real `monkeyc` compile cannot run here,
# because the SDK must never be baked into the image, but an unresolved shared
# library is exactly what breaks the compile, and `ldd` sees it at build time.
RUN java -version \
    && JAVA_HOME="$(find /usr/lib/jvm -mindepth 1 -maxdepth 1 -type d | head -n 1)" \
    && ldd "$JAVA_HOME/lib/libfontmanager.so" > /tmp/libfontmanager.ldd \
    && if grep -q "not found" /tmp/libfontmanager.ldd; then \
         echo "error: libfontmanager.so has unresolved shared libraries:" >&2; \
         grep "not found" /tmp/libfontmanager.ldd >&2; \
         exit 1; \
       fi \
    && rm -f /tmp/libfontmanager.ldd

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
    E2E_ARTIFACTS=/artifacts \
    CIQ_SDK_WORKDIR=/tmp/ciq-sdk \
    DISPLAY=:99

WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
