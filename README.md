# openHAB for Garmin

An app that connects your Garmin wearable to your [openHAB](https://www.openhab.org) smart home system, giving you convenient access to essential devices and real-time information.

<a href="https://apps.garmin.com/apps/d83b5e1b-4a12-4eda-bbad-26f88952a959" target="_blank" class="external" style="margin-top: auto; margin-bottom: auto; margin-right: 20px;">
  <img alt="Get it on Connect IQ Store" src="docs/images/connect_iq_badge.svg" width="240px">
</a>

The user manual is available in the openHAB documentation at <https://next.openhab.org/docs/apps/garmin/>.

---

## Table of Contents

This README covers the following topics:

- [Introduction](#introduction)
- [Project Structure](#project-structure)
  - [Root Folder `/`](#root-folder-)
  - [Folder `.vscode`](#subfolder-vscode)
  - [Folder `/docs`](#subfolder-docs)
  - [Folder `/resources`](#subfolder-resources)
  - [Folder `/source`](#subfolder-source)
  - [Folder `/tests`](#subfolder-tests)
- [Build Instructions](#build-instructions)
  - [To Run the App in the Garmin Simulator](#to-run-the-app-in-the-garmin-simulator)
  - [To Compile for a Single Device](#to-compile-for-a-single-device)
  - [Generating the `.iq` File for Garmin Connect IQ Store Upload](#generating-the-iq-file-for-garmin-connect-iq-store-upload)
  - [To Add a New Device](#to-add-a-new-device)
- [End-to-End Tests](#end-to-end-tests)
- [Unit Tests](#unit-tests)
- [Helpful Tips and Notes](#helpful-tips-and-notes)
- [Copyright Notice](#copyright-notice)
- [Attributions](#attributions)

---

## Introduction

This app is built using the Garmin Connect IQ SDK and is based on [openHAB sitemaps](https://www.openhab.org/docs/ui/sitemaps.html).

It includes two application types:

- _**Glance**_: A lightweight, quick-access entry point.
- _**Widget**_: The main, full-screen interface for interacting with the sitemap.

### _Widget_ Overview

The _Widget_:

- Displays a hierarchical structure representing a single sitemap, using the SDK's `CustomMenu` and `CustomMenuItem`.
- Requests sitemap updates at a configurable interval.
- Updates the menu whenever a new response is received.
- Caches the latest sitemap response in persistent storage.
- Renders sitemap *Widget*s using specific `CustomMenuItem` implementations, which may also send commands to items.

### _Glance_ Overview

The _Glance_:

- Displays the sitemap label from persistent storage, falling back to a static label if none has been stored yet.
- Does not perform network requests itself. The sitemap and its label are stored by the _Widget_, so the _Glance_ only reflects the data cached by the last _Widget_ session.

#### Further reading:

- [openHAB for Garmin User Manual](https://next.openhab.org/docs/apps/garmin/)
- [Connect IQ for Developers](https://developer.garmin.com/connect-iq)
- [Connect IQ SDK](https://developer.garmin.com/connect-iq/sdk/)

---

## Project Structure

The project is organised into the following directories:

### Root Folder `/`

The root directory contains essential project files.

#### Key files

- `README.md`: This file
- `manifest.xml`: Defines the app's structure, supported devices, and required permissions (e.g., storage, background tasks)
- `monkey.jungle`: The build script that determines available features per device. It includes extensive documentation in the file itself.

#### Further reading

- [Connect IQ SDK Core Topics - Manifest and Permissions](https://developer.garmin.com/connect-iq/core-topics/manifest-and-permissions/)  
- [Connect IQ SDK Core Topics - Build Configuration](https://developer.garmin.com/connect-iq/core-topics/build-configuration/)

### Subfolder `.vscode/`

Contains VS Code customizations.

### Subfolder `docs/`

Contains the user manual, published at <https://next.openhab.org/docs/apps/garmin/>.

### Subfolder `resources/`

In the Connect IQ SDK, resources define:

- **Properties**: Parameters stored outside the app. The app uses them for the user-facing settings defined in `settings.xml` (URL, sitemap name, credentials, polling interval, and so on). Device-specific configuration that must update with the app is implemented as code-based **Constants** instead. See [Folder `source/config`](source/config/README.md) for details.

- **Settings**: User-facing configurations
- **Drawables**: Image assets used by the app
- **Strings**: User-facing text values

#### Folder breakdown

- `/resources/base`: Contains resources shared across all device types.
  - `/resources/base/settings.xml`: Specifies the app’s configuration settings, including default values, types, and user-facing descriptions.
  - `/resources/base/strings.xml`: Contains the user-facing display strings and error messages used by the app. Hardcoded user-facing text in Monkey C code should be avoided; add a string resource instead. Error messages use `scope="background"` so they remain available during Wi-Fi sync, in the background service, and in glance mode.
  - `/resources/base/version.xml`: Contains the app name and version number.
- `/resources/edge`: Contains resources specific to Garmin Edge devices. Some `Drawable` elements are overridden here to account for different sizing requirements.
- `/resources/icons-interface`: Contains the `icons-interface.xml` file defining the icons used in the app’s user interface, primarily for navigation, along with their corresponding SVG files.
- `/resources/icons-launcher`: Contains one subdirectory per launcher icon size. Each subdirectory includes a `drawables.xml` resource definition for that specific size. The correct icon is selected via the resource path in the `monkey.jungle` build file, depending on the target device.
- `/resources/icons-widgets`: Contains the `_icons-widgets.xml` file defining the openHAB icons that can be assigned to widgets, along with their corresponding SVG files.

#### Further reading

- [Connect IQ SDK Core Topics - Resources](https://developer.garmin.com/connect-iq/core-topics/resources/)

### Subfolder `source/`

The app is written in **Monkey C**, Garmin's programming language, using the Connect IQ SDK API for UI, settings, persistent storage, and HTTP requests to openHAB.

For more details, continue reading the folder’s [README](source/README.md).

### Subfolder `tests/`

Contains the automated end-to-end test harness: a Docker Compose stack with openHAB, a request-recording proxy and a simulator runner, plus the openHAB fixtures, the scenario YAML and the Python runner.

The Connect IQ SDK is **not** part of this folder or of any image built from it; it is mounted at run time. See the folder’s [README](tests/README.md).

---

## Build Instructions

Follow the steps below to build, run, and test the app using the Connect IQ SDK and Garmin simulator.

### To Run the App in the Garmin Simulator

1. Install:
   - Visual Studio Code
   - Git
   - [Connect IQ SDK](https://developer.garmin.com/connect-iq/sdk/) (see [Connect IQ SDK Installation](docs/SDK_INSTALLATION.md) for troubleshooting)
   - Monkey C extension for VS Code

1. Open VS Code and clone the `openhab-garmin` repository
1. Open any file in the `/source` folder
1. Press **F5** to start the simulator and select a target device
1. Go to `File` → `Edit Persistent Storage` → `Edit Application.Properties` and configure as needed  
   - To allow HTTP URLs, uncheck `Settings > Use Device HTTPS Requirements`

### To Compile for a Single Device

1. Press `CTRL+SHIFT+P` → `Monkey C: Build Current Project`

### Generating the `.iq` File for Garmin Connect IQ Store Upload

Follow these steps to prepare and export your app for upload to the Garmin Connect IQ Store:

1. **Remove Debug Statements**

   Ensure all debug logging or print statements are commented out or removed.
   See [Removing Debug Statements](#removing-debug-statements) for guidance.

1. **Update the Version Number**

   Edit the file `resources/base/version.xml` and set the correct version number for this release.

1. **Set the Correct App ID**

   Open `manifest.xml` and set the appropriate app ID:

   - Use the _stable_ app ID for production releases.
   - Use the _beta_ app ID for test releases.

   > 🛠 You’ll need to switch to XML mode in your editor to change this value manually.

1. **Export the Project**

   Use the command palette:
   `CTRL + SHIFT + P` → `Monkey C: Export Project`
   This will generate the `.iq` file used for uploading.

### To Add a New Device

To support a new device:

1. If the device isn't available in your current SDK, launch the **SDK Manager** and download/activate the latest version.
1. In `manifest.xml`, check the new device in the supported device list.
1. Configure device-specific features in the `monkey.jungle` build file (see [Root Folder `/`](#root-folder-)).
1. Test in the simulator (see [above](#to-run-the-app-in-the-garmin-simulator))
1. Export the project (`CTRL+SHIFT+P` → `Monkey C: Export Project`) and upload the `.iq` file to the Connect IQ Store

#### Further reading

- [Connect IQ SDK - Compatible Devices](https://developer.garmin.com/connect-iq/compatible-devices/)
- [Connect IQ SDK - Device Reference](https://developer.garmin.com/connect-iq/device-reference/)

---

## End-to-End Tests

The `tests/` folder contains an automated end-to-end harness: a pinned openHAB instance, a shim that records every request the app makes (and can inject scripted faults), and a runner that builds the PRG with the Linux `monkeyc` and drives the Connect IQ simulator under Wine.

```bash
cd tests/compose
cp .env.example .env          # set CIQ_SDK_HOST_PATH to your Connect IQ SDK
cd ../runner
python -m pip install -r requirements.txt
python -m e2e pin-images --write
python -m e2e status
```

The harness unit tests run without Docker or the SDK:

```bash
cd tests/runner
python -m pytest
```

The Connect IQ SDK is never committed and never baked into an image: it is mounted read-only and validated against `tests/config/sdk.version`. See [tests/README.md](tests/README.md) for the full instructions, the scenario list and the current limitations.

---

## Unit Tests

`source/test/` contains Connect IQ unit tests (Run No Evil) for the code that does
not need a view, input or network: sitemap JSON parsing, error classification and
element parameters. They live in the app's source path so the device build matrix
typechecks them, and the compiler removes them from every normal build.

```bash
export CIQ_SDK_ROOT=/path/to/connectiq-sdk-9.3.0
tests/unit/run                  # all tests on fenix847mm
```

They run only in the simulator and cannot assert rendering, input or lifecycle
behaviour; that stays with the E2E harness. See
[tests/docs/unit-tests.md](tests/docs/unit-tests.md) for the feasibility evidence,
the split between the two tiers and what remains untestable.

---

## Helpful Tips and Notes

A collection of useful information for working on this project.

### Removing Debug Statements

While debug statements are only printed in debug builds, they still occupy code space in release builds. Therefore, when building a release, all debug statements should be commented out.

To find all active debug statements in VS Code, press `CTRL+SHIFT+H` to open the global search and replace panel. Enable regular expressions by clicking the `.*` icon next to the search field, and use the following pattern to locate all active debug statements:

```regexp
(?<!\/\/ )Logger\.debug\(
```

To deactivate the debug statements, replace them with:

```text
// Logger.debug(
```

This will comment them out without affecting any lines that are already commented.

---

## Copyright Notice

```text
Copyright (c) 2025 Contributors to the openHAB project

The contents of this repository are made available under the
terms of the Eclipse Public License 2.0 which is available at
http://www.eclipse.org/legal/epl-2.0

SPDX-License-Identifier: EPL-2.0
```

---

## Attributions

The [interface icons](resources/icons-interface), except for the openHAB logo, are licensed under CC BY 3.0 by [Adrien Coquet](https://adrien-coquet.com/) from [Noun Project](https://thenounproject.com/creator/coquet_adrien/).
