# Folder `sitemap`

Defines the data model used to parse and represent sitemap content received from openHAB.

The classes in this folder convert the JSON representation delivered by openHAB into a structured object model. This model serves as the foundation for building and rendering the user interface.

See the user manual for details: [User Manual – Sitemap Setup](https://next.openhab.org/docs/apps/garmin/#sitemap-setup)  

---

## Key Concepts

The sitemap data structure represents a single sitemap response received from openHAB. It is therefore inherently transient and exists only until the next response is received from the server, at which point it is fully replaced.

As a general rule, the sitemap data structure is not modified after it has been created. Instead, it reflects exactly the state provided by the server.

There is one exception to this rule. When a command is sent successfully, the UI immediately reflects the expected new state without waiting for the next server response. This behavior is controlled by the corresponding menu items, which update the sitemap data structure locally to match the anticipated state. We refer to this as an internal update.

There is one additional, deliberate deviation from the server data. For Group elements whose item type maps to a controllable widget, the group page contains a derived widget representing the Group item itself. It is created from a shallow copy of the Group element without its `linkedPage`, so it acts as a command entry instead of a navigation entry. The server-provided JSON is not modified. The derived widget uses the group label by default; the Garmin-specific `Group label|Control label` convention can override it. See `SitemapGroupPage`, `SitemapWidgetFactory.createGroupControl()`, and `SitemapElement.splitGroupLabel()`.

---

## Folder Structure

### Subfolder `base/`

Contains `SitemapElement.mc`, the abstract base class for all sitemap elements.

It defines shared properties and common parsing logic used by all concrete sitemap element types.

### Subfolder `json/`

The Connect IQ API delivers JSON data as a dictionary.  
`JsonObjectAdapter` provides type-safe accessors for retrieving objects, arrays, strings, numbers, and booleans from the JSON structure.

`JsonComparer` provides a recursive structural comparison of two JSON values. `SitemapComparison` builds on it to compare a received sitemap with the stored one. The comparison is split over several asynchronous tasks (the root first, then one task per top level element), so that no single task blocks the UI for the whole sitemap. It is used to detect that a received sitemap is identical to the stored one, so that the data layer and the menu do not need to be rebuilt.

This layer isolates JSON handling details from the higher-level sitemap model.

### Subfolder `pages/`

Contains classes representing the **content** of container-type sitemap elements (e.g., frames or groups).

These classes model the internal structure and child elements of a container. They do not represent the container widget itself, but rather the content that is displayed within it.

To keep the UI responsive and avoid limitations such as the Watchdog timeout for long-running code and stack size restrictions caused by recursive processing, pages are processed asynchronously and non-recursively. The work is split into small tasks that are executed by the task queue implementation.

Empty nested containers are valid. openHAB returns an empty `widgets` array for a `Frame` without children and for containers whose children are all hidden by `visibility`. `SitemapContainer.hasWidgets()` records whether the received JSON contained children (or whether a derived widget such as the Group control was added). Do not use `getWidgets().size()` for this check: widgets are created asynchronously, so an empty array may only mean that the page is still being built. The UI uses `hasWidgets()` to show a toast instead of opening an empty page.

### Subfolder `widgets/`

Contains `SitemapElement` subclasses representing concrete sitemap widgets, such as switches, sliders, text labels, and container elements.

For container-type elements (e.g., frames or groups), the widget class describes the container itself and links to the corresponding implementation in `pages/` to represent its content.

The item of a `Text` element is optional, as openHAB allows static text without an item. Such an element displays only its label and exposes no item through `SitemapWidget.getItem()`.

#### Folder Structure

- **`base/`**  
  Contains `SitemapWidget`, the base class for all widget-type elements.  
  Also includes the `Item` base class representing an openHAB item, as well as helper classes for parsing colors and icons.

- **`factory/`**  
  Contains `SitemapWidgetFactory`, responsible for instantiating the appropriate `SitemapElement` subclass based on a given JSON object.

- **Concrete widget directories**  
  Each directory implements specific sitemap element types.

  In some cases, the `SitemapWidgetFactory` maps multiple openHAB sitemap element types to a single implementation.  
  For example, `numeric/` handles both _Setpoint_ and _Slider_ elements.
