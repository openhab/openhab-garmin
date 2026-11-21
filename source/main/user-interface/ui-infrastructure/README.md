# Folder `ui-infrastructure`

Contains central, stateful runtime facilities that support the user interface.

These classes are not reusable helper functions or visible UI components. Instead, they provide shared management services that are accessed by all UI modules. They coordinate and maintain global UI behavior, such as navigation flow, resource management, and dynamic UI state tracking.

Unlike `shared`, which provides reusable visual building blocks, `ui-infrastructure` implements the underlying mechanisms that enable and coordinate the UI as a whole.

---

## Subfolder `bitmap-cache/`

In the sitemap, elements can define icons that are displayed by menu items.

To avoid repeatedly instantiating identical bitmap resources, the `BitmapCache` provides centralized caching and reuse. Menu items request icons through this cache, ensuring that bitmap instances are shared where possible, reducing memory usage and improving performance.

---

## Subfolder `night-mode/`

On Edge devices, both light and dark (night) modes are supported, and the mode can change at runtime.

Since querying the API’s night mode state is relatively costly, the `NightModeTracker` stores the current mode internally. The system invokes `OHApp.onNightModeChanged` when the mode changes, which triggers the tracker to update its internal state. Other UI components access this centralized state instead of querying the API directly.

---

## Subfolder `view-stack/`

`ViewStack` is a centralized navigation manager that must be used for switching between views instead of calling `WatchUi` functions directly.

It maintains its own internal view stack in parallel with the Connect IQ API’s view stack. This enables advanced behavior, such as replacing the entire stack with a new base view, and works around a known issue on certain newer devices where calling `WatchUi.getCurrentView()` can lead to crashes.

Each view stack entry also carries a scope (`MAIN` or `SETTINGS`) that identifies the part of the app the view belongs to. A pushed or switched view inherits the scope of the view it replaces, and popping a view restores the scope of the view below it. This makes `ViewStack.getCurrentScope()` the single source of truth for section visibility: any future settings view automatically remains part of the settings scope, even after stack changes. `ViewStack.pushViewInScope()` is used to enter a section, while `resetTo()` and `registerInitialView()` start in the `MAIN` scope.

`ViewStack.popView()` only removes views above the base view. Leaving the app is therefore handled explicitly in `HomepageMenuDelegate.onBack()`, so that a stray or duplicate back event cannot discard the base view and terminate the app. An attempted pop below the base view is reported as a non-fatal UI exception, which is shown to the user as a toast.

`ViewStack.contains()` allows sitemap updates to detect whether a page that became empty is still part of the current navigation path, so it can be removed instead of leaving an empty menu in the stack.

See GitHub ticket [#274](https://github.com/openhab/openhab-garmin/issues/274) for details.
