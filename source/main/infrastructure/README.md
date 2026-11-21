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
- The active/inactive lifecycle on devices that keep the app running when it is
  hidden (e.g. when the watch face is shown), see `AppLifecycle`

### Subfolder `exceptions/`

Defines all custom exception types used by the app. Most exception classes extend the base class `Exception`. `GeneralException` serves as the base for general errors, and remains the parent of the more specific fatal exceptions.

### Subfolder `logging/`

Contains the `Logger` class, which provides utility methods to output debug statements and exceptions to the system log.

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
