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

This layer isolates JSON handling details from the higher-level sitemap model.

### Subfolder `pages/`

Contains classes representing the **content** of container-type sitemap elements (e.g., frames or groups).

These classes model the internal structure and child elements of a container. They do not represent the container widget itself, but rather the content that is displayed within it.

To keep the UI responsive and avoid limitations such as the Watchdog timeout for long-running code and stack size restrictions caused by recursive processing, pages are processed asynchronously and non-recursively. The work is split into small tasks that are executed by the task queue implementation.

### Subfolder `widgets/`

Contains `SitemapElement` subclasses representing concrete sitemap widgets, such as switches, sliders, text labels, and container elements.

For container-type elements (e.g., frames or groups), the widget class describes the container itself and links to the corresponding implementation in `pages/` to represent its content.

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
