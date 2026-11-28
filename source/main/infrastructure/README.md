# Folder `infrastructure`

Provides the technical foundation of the application.

It contains the main `OHApp` class responsible for managing the application lifecycle, along with cross-cutting services such as logging, memory management, task coordination, and other shared technical utilities.

---

## Folder Structure

### Subfolder `appbase/`

Contains the `OHApp` class, the main entry point for the app. It handles:

- Initialization of the initial view in both _Glance_ and _Widget_ modes
- Startup and shutdown logic
- Reactions to app updates and settings changes
- The app state — widget active, widget inactive, or glance — including the
  active/inactive lifecycle on devices that keep the app running when it is
  hidden (e.g. when the watch face is shown), see `AppLifecycle`

Settings are read only once, when the app starts. Connect IQ provides no way to
restart the app, and the singletons have already cached the previous URL,
sitemap, authentication data and polling interval. A settings change while the
app is running therefore stops sitemap polling, cancels in-flight requests,
discards the cached sitemap and menu, and asks the user to close and reopen the
app. While the app is hidden, this message is deferred until it becomes visible
again. If the callback reaches a process without an initial view (for example
one that was only instantiated to deliver it), only the persisted data is
discarded, so the next start reads the new settings and starts normally.

### Subfolder `exceptions/`

Defines all custom exception types used by the app. Most exception classes extend the base class `Exception`. `GeneralException` serves as the base for general errors, and remains the parent of the more specific exception classes. `SettingsChangedException` is shown when the app settings changed while the app was running, see above. `CommunicationBaseException.requiresFullScreenError()` only decides how a communication error is presented; sitemap polling continues so the app can recover without a restart.

### Subfolder `logging/`

Contains the `Logger` class, which provides utility methods to output debug statements and exceptions to the system log.

It also contains `E2ETrace`, which provides debug-only, machine-readable trace events for end-to-end tests. Events are printed as single lines of the form `E2E:<event>;<field>=<value>[;<field>=<value>...]` and cover view changes, the focused menu entry, connection mode changes, sitemap and command outcomes, error handling classification, toasts, full-screen errors, and the active system language at startup. The focus event is emitted when the highlighted entry changes and is what lets a scenario address an element by label instead of by a press count; it reports the entry's menu, position, sitemap element type and percent-encoded sitemap label, and never translated chrome. The event schema is documented in the class header. All `(:release)` implementations are empty and contain no event text, so release builds neither print nor retain any E2E data. `E2ETraceExceptions` maps exceptions to the stable identifiers and codes used by these events and is only referenced from debug-only helpers.

### Subfolder `memory/`

Contains the `MemoryManager` class.

The `MemoryManager` allows callers to validate available memory before performing memory-intensive operations. If the available memory is below a safe threshold, it throws a controlled exception, enabling the application to react appropriately and avoid an out-of-memory crash.

### Subfolder `settings/`

Contains the `AppSettings` class, which reads, validates, and exposes application settings through static accessor methods.

When sideloading the app directly onto a device, application settings are not available through the standard mechanism. For this scenario, the folder also includes an alternative implementation that can be activated by renaming the file. This fallback implementation provides hardcoded settings to allow the app to run in sideloaded environments.

#### Further Reading

- [Connect IQ SDK Core Topics – Resources](https://developer.garmin.com/connect-iq/core-topics/resources/)
- [Connect IQ API Docs – `Toybox.Application.Properties`](https://developer.garmin.com/connect-iq/api-docs/Toybox/Application/Properties.html)
- See also: [Folder `resources`](../../../README.md#subfolder-resources)
