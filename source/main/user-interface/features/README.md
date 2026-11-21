# Folder `features`

Contains the primary user-facing functionality of the application.

This folder implements the main UI features exposed to the user. Each feature represents a concrete capability of the app and is structured as an independent UI module.

Currently, three feature categories are implemented:

- **Glance** – A compact view shown in the device’s glance carousel.  
- **Settings** – A menu providing app status information and maintenance actions.  
- **Sitemap** – The core feature of the app, displaying and interacting with openHAB sitemap elements.

---

## Key Concepts

The main UI is built on top of `CustomMenu`, a customizable variant of `Menu2` that leverages the native UI controls of the watch.

Sitemap widgets are implemented as `CustomMenuItem` instances. These display the current state of an element and may either:

- Provide direct interaction (e.g., toggle switches), or  
- Open full-screen control views for more complex interactions.

The _Settings_ menu is also implemented using `CustomMenu`.

### Further Reading

- [Connect IQ Core Topics – Native UI Controls](https://developer.garmin.com/connect-iq/core-topics/native-controls/)  
- [Connect IQ API Docs – `Toybox.WatchUi.CustomMenu`](https://developer.garmin.com/connect-iq/api-docs/Toybox/WatchUi/CustomMenu.html)

---

## Folder Structure

### Subfolder `base/`

Provides abstract and shared base classes for menu implementations:

- `BaseMenu`  
  Extends `CustomMenu` and applies consistent styling across all menus in the app.

- `BaseMenuItem`
  Extends `CustomMenuItem` to provide consistent behavior and styling support across the app.  
  It draws the item background and divider, applies the current theme, and enables the focus indicator used on Garmin Edge devices.

- `LabelMenuItem`  
  Extends `CustomMenuItem` to provide a simple, non-interactive menu entry consisting of a label and a sub-label. This is primarily used in the settings menu for informational entries.

- `StructuredMenuItem`  
  Extends `CustomMenuItem` to provide a structured menu entry composed of an icon, label, textual state, bitmap state, and an optional action icon. Not all elements need to be used in every instance.  
  This class serves as the foundation for all sitemap widgets and is also used in the settings menu for interactive entries.
  
### Subfolder `glance/`

Implements the Glance feature.

For detailed information, see the folder’s [README](glance/README.md).

### Subfolder `settings/`

Implements the _Settings_ feature.

For detailed information, see the folder’s [README](settings/README.md).

### Subfolder `sitemap/`

Implements the core UI for displaying and interacting with openHAB sitemap content.

This includes:

- The main sitemap menu  
- Menu items representing sitemap elements  
- Full-screen control views for complex interactions  

For detailed information, see the folder’s [README](sitemap/README.md).
