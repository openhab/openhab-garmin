# Ubuntu 22.04 + Wine + the native GTK simulator + Xvfb + xdotool + ImageMagick
# + Python 3.
#
# Everything the harness needs to compile the PRG with the Linux `monkeyc`
# (inside the mounted SDK) and to drive **both** Connect IQ simulators: the
# Windows build under Wine (the default, `--driver wine`) and the native GTK
# build from the Linux SDK (`--driver native`). The base is 22.04 and not 24.04
# because the native binary links `libwebkit2gtk-4.0`, which 24.04 dropped; the
# two driver package sets therefore live in one image. Moving the base also
# moved Wine from 24.04's 9.0 to 22.04's 6.0.3; that is a named risk in
# tests/docs/simulator-driver.md, and the smoke check below fails here rather
# than at a launch.
#
# The SDK itself is never copied in at build time: it is mounted read-only at
# /opt/ciq-sdk, copied to a writable path at container start (see
# entrypoint.sh) and validated there. That includes the native simulator
# binary, so nothing Garmin-licensed is baked into an image.
#
# The Connect IQ device font pack is handled the same way, for the same reason:
# it is licensed Garmin assets, mounted read-only at /opt/ciq-fonts and staged
# by entrypoint.sh into $HOME/.Garmin/ConnectIQ/Fonts. Without it the simulator
# cannot resolve a device face and the app crashes on its first draw with
# "Invalid Font Specified", so it is never baked into the image either.
#
# Build context is tests/ (see compose.yaml).

ARG RUNNER_BASE_IMAGE=ubuntu:22.04
FROM ${RUNNER_BASE_IMAGE}

LABEL org.opencontainers.image.title="openhab-garmin E2E runner" \
      org.opencontainers.image.description="Builds the PRG and drives the Connect IQ simulator under Wine or natively" \
      org.opencontainers.image.licenses="EPL-2.0"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# wine32:i386 is required: the simulator is a 32-bit Windows binary, and a
# wine64-only install fails at launch with an opaque loader error.
#
# default-jre, not default-jre-headless: monkeyc rasterises SVG drawables
# through Batik, whose CursorManager initialises AWT, and AWT on Linux loads
# libawt_xawt.so. On 22.04 that library ships only with the non-headless JRE
# (24.04's headless JDK happened to carry it), and without it a compile dies
# with UnsatisfiedLinkError after the image has looked healthy. The build check
# below names the file when a custom base omits it.
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
#
# The list carries both drivers while the fallback exists: wine/wine64/wine32
# plus Wine's GL/X libraries for `--driver wine`, and libgtk-3-0,
# libwebkit2gtk-4.0-37, libsecret-1-0, libnotify4, libusb-1.0-0 and
# librsvg2-common for `--driver native` (librsvg2-common supplies the Adwaita
# icons; x11-xkb-utils supplies xkbcomp, which GTK uses for the keymap).
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        default-jre \
        fontconfig \
        libfontconfig1 \
        libfreetype6 \
        libharfbuzz0b \
        openssl \
        python3 \
        python3-venv \
        xvfb \
        x11-utils \
        x11-xkb-utils \
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
        libgtk-3-0 \
        libwebkit2gtk-4.0-37 \
        libsecret-1-0 \
        libnotify4 \
        libusb-1.0-0 \
        librsvg2-common \
    && rm -rf /var/lib/apt/lists/*

# The native simulator aborts its first draw on an Adwaita SVG when the pixbuf
# loader cache does not match the installed loaders. Regenerating it is the
# recorded fix (tests/docs/simulator-driver.md), and doing it at build time
# keeps it out of the driver.
RUN /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders \
        > /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/loaders.cache

# Both drivers have to be usable in this one image while Wine is the fallback:
# Wine for the Windows simulator, and the GTK/WebKit stack for the native one.
# A missing piece fails the build with the name of what is missing, instead of a
# launch minutes later. `wine --version` needs no display and no prefix.
RUN wine --version \
    && ldconfig -p | grep -q 'libgtk-3.so.0' \
    && ldconfig -p | grep -q 'libwebkit2gtk-4.0.so.37' \
    && test -s /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/loaders.cache

# Smoke check for the JRE the compiler uses, which a real `monkeyc` compile
# cannot do here because the SDK must never be baked into the image. Two
# libraries only appear when the compiler actually runs, and both have caused a
# live build failure: libharfbuzz (AWT's font manager) and libawt_xawt (Batik's
# AWT initialisation, missing from the headless JRE on 22.04). The decision rule
# lives in e2e/fontmanager.py and is unit-tested; the ldd calls use the JVM's own
# loader path, because plain ldd reports libjvm.so as missing and failed a
# healthy image in the first live run.
COPY runner/e2e/fontmanager.py /usr/local/bin/fontmanager_check.py
RUN java -version && python3 /usr/local/bin/fontmanager_check.py

# A venv keeps the harness dependencies out of the distro packages, and keeps
# the image independent of PEP 668's "externally managed" marker.
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
# be correct for the CLI to import itself and to find the shared shim grammar.#
# CIQ_SDK_ROOT is declared here and points at the copy, not at the read-only
# mount: `docker exec` starts a new process from the image environment and never
# sees an `export` from the entrypoint, so every consumer (exec, a driver, the
# CLI) would otherwise fail with "Connect IQ SDK not configured". It must equal
# CIQ_SDK_WORKDIR; tests/test_runner_image.py asserts that they cannot drift.
ENV PYTHONPATH=/work/tests/runner:/work/tests \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    E2E_ARTIFACTS=/artifacts \
    CIQ_SDK_WORKDIR=/tmp/ciq-sdk \
    CIQ_SDK_ROOT=/tmp/ciq-sdk \
    DISPLAY=:99

WORKDIR /work
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
