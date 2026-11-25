# Folder `source`

This folder contains the source code of the app, written in **Monkey C**, Garmin’s programming language. It uses the Connect IQ SDK for user interface components, application settings, persistent storage, and HTTP communication with the openHAB server.

---

## Key Concepts

### Tailoring

Two mechanisms are used to tailor the source code for different target devices:

- The relevant `config` and `themes` directories are added to the device-specific source path in [`monkey.jungle`](../README.md#root-folder-).
- In the shared `main` source, exclude annotations are used to adapt the code to individual target devices. These annotations are mapped to devices in [`monkey.jungle`](../README.md#root-folder-) and are documented there.

### Other Annotations

- `(:glance)` marks code that must be included in the _Glance_ view.
- `(:test)` marks Connect IQ unit tests. The compiler removes test code from
  debug and release builds, so it only runs when the build uses `--unit-test`
  and `monkeydo` is started with `-t`. See [Folder `test/`](#subfolder-test-).

### **Inline Documentation**

All `.mc` files include inline comments and documentation describing the purpose and internal workings of each class.

### **Further Reading**

- [Connect IQ Core Topics](https://developer.garmin.com/connect-iq/core-topics/)
- [Connect IQ API Docs](https://developer.garmin.com/connect-iq/api-docs/)

---

## Folder Structure

### Subfolder `config/`

This folder defines device-specific configuration using code-based _Constants_, which replace Garmin SDK _Properties_ due to their limitations. Unlike _Properties_, _Constants_ are embedded in the code, update automatically with new app versions, and benefit from compiler validation.

For more details, continue reading the folder’s [README](config/README.md).

### Subfolder `main/`

Contains the core application source code that is shared across all target devices. This includes the data layer, infrastructure and service components, as well as the user interface logic and views.

Although the `main` code is designed to be largely device-agnostic and to rely on `config` and `themes` for device-specific behavior, there are a few places where different implementations are required depending on device characteristics, such as button-based versus touch-based interaction or round versus rectangular displays. In these cases, device-specific code paths are introduced using exclude annotations directly within the source.

For additional details, refer to the folder’s [README](main/README.md).

### Subfolder `themes/`

Themes define the color schemes used in the app and can change dynamically at runtime. This also includes icons whose colors must be adapted to the active theme. Themes are used to implement the light and dark mode on Edge devices.

For more details, continue reading the folder’s [README](themes/README.md).

### Subfolder `test/`

Contains the Connect IQ unit tests (`(:test)` functions), split by tier: sitemap
model parsing, error classification, and element parameters. They are part of
the base source path in [`monkey.jungle`](../README.md#root-folder-), so every
device build typechecks them, and the compiler strips them from normal builds.

They need no view, input or network, and they do not test rendering. Run them
with [`tests/unit/run`](../tests/unit/run); the design note, evidence and limits
are in [`tests/docs/unit-tests.md`](../tests/docs/unit-tests.md).
