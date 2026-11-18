# Folder `config`

This folder defines device-specific configuration via code-based constants, replacing Garmin SDK _Properties_, which have significant limitations.

---

## Why Constants?

The Garmin SDK’s _Properties_ are stored externally from the app and persist across installations. This means that when a new version of the app is installed, any existing properties from the previous version remain, unless the user performs a full uninstall and reinstall. To avoid this issue and allow reliable updates, the app uses _Constants_ instead:

- Constants are part of the code base and automatically update with new app versions.
- The compiler validates constant usage, reducing runtime errors.
- Constants can be cleanly overridden for specific devices.

---

## Key Concepts

All devices inherit default values from `BaseConfig` and `GlanceBaseConfig`, which define all configuration values used in the _Widget_ and _Glance_, respectively.

In the source code, these are accessed via the `Config` and `GlanceConfig` classes. Each supported device may override the defaults by providing its own implementation of these classes.

Additional base classes can be introduced for specific device families. For example, `EdgeBaseConfig` inherits from `BaseConfig` and overrides values that apply to all Edge devices. Concrete `Config` implementations for individual Edge models can then further override selected values defined in the Edge base configuration.

---

## Folder Structure

- `base/`:
  Contains `BaseConfig` and `GlanceBaseConfig`, which define the baseline values for all devices.
- `device-default/`:
  Contains default `Config` and `GlanceConfig` implementations. These inherit all values from the base without overrides.
- `device-<device-name>/`:
  Contains device-specific implementations of `Config` and `GlanceConfig`, overriding select values to adapt the UI for the particular device. A folder may also cover a whole device family, for example `device-gradient-focus/`, which is shared by all models whose native menu uses a gradient background as the selected-item indicator (Fenix 8 and later, Forerunner x65 and later). Variants such as `device-gradient-focus-25deg-select/` combine such family-specific values with per-device values, in this case the input hint positions.
