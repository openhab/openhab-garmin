# Folder `widgets`

Contains the concrete widget implementations. Each widget is implemented as a menu item. Some widgets additionally provide command selection menus or full-screen control views that are opened when the menu item is selected.

This folder also includes the factory responsible for instantiating the correct menu item for a given sitemap element from the data layer.

---

## Key Concepts

- **MenuItem classes** (e.g. `OnOffSwitchMenuItem`)  
  Implement interactive list entries that represent sitemap elements.

- **Drawable classes**  
  Render visual components, such as status indicators (e.g. `OnOffStateDrawable` for switch states).

- **Command selection menus**  
  Allow the user to choose from a predefined list of commands associated with a widget.

- **Control views**  
  Full-screen views that provide widget-specific state visualization and interaction. They support more advanced control patterns, including repeated commands, such as scrolling through a sequence of numeric values while continuously updating the state.

The base class `StructuredMenuItem` defines the common layout and structure of all menu items, including:

- An optional left-side icon (`Drawable`)  
- A central text label  
- An optional right-side text state  
- An optional right-side bitmap status indicator implemented via a `Drawable`  
- An optional action indicator signaling that the item is interactive or opens another page  

Apart from the label, all elements are optional and can be combined depending on the widget’s requirements.

In addition to rendering the visual representation, menu items handle user input and execute commands using a `BaseCommandRequest` (either `NativeCommandRequest` or `WebhookCommandRequest`). If a full-screen control view is opened, that view delegates command execution back to the associated menu item.

---

## Folder Structure

### Subfolder `container/`

Implements the widget for container elements such as Frames and Groups. Selecting a container widget opens a submenu containing the elements within that container.

See also [Frames](https://next.openhab.org/docs/apps/garmin/#frame) and [Nested Elements](https://next.openhab.org/docs/apps/garmin/#nested-elements) in the user manual.

---

### Subfolder `factory/`

Contains the `MenuItemFactory`, which is the single source of truth for selecting the concrete menu item class for a given sitemap element. The same selection is used when creating a menu item and when checking whether an existing menu item can be reused during a sitemap update.

---

### Subfolder `numeric/`

Implements the widget for Setpoint and Slider elements. While other UIs differentiate between the two, the constraints of the wearable interface make a unified, Setpoint-style interaction model more suitable.

The menu item displays the current state and, when selected, opens a full-screen view based on the `DynamicPicker` from [`shared/picker/`](../shared/picker/) with a widget-specific data source and delegate from `numeric-picker/`.

See also [Setpoint and Slider](https://next.openhab.org/docs/apps/garmin/#setpoint-and-slider) in the user manual.

---

### Subfolder `settings/`

Implements the settings entry. This is a special case, as it is not a sitemap widget. On touch-based devices, and on button-based Edge devices that show the settings as a regular menu item due to a firmware issue, selecting this entry opens the settings menu. On other button-based devices, the settings menu is accessed by scrolling past the footer with the settings icon on the root homepage level.

See also [Settings Menu](https://next.openhab.org/docs/apps/garmin/#settings-menu) in the user manual.

---

### Subfolder `switch/player/`

Implements `PlayerView`, enabling interaction with media players such as play, pause, next, and previous, while also displaying the current playback state.

See the [Player section](https://next.openhab.org/docs/apps/garmin/#player) for usage details and examples.

---

### Subfolder `switch/`

Contains multiple widgets covering both Switch and Selection sitemap elements. The concrete implementation is chosen based on available commands and item types. The selection is re-evaluated on every sitemap update, so a widget keeps the same representation even when sitemap entries shift positions (e.g. due to `visibility` changes).

- `switch/base/`  
  Provides a shared base class with reusable functionality used by all switch-related widgets.

- `switch/generic/`  
  Used for items whose available commands are not covered by the toggle rules. This includes non-`Switch` items with commands that do not represent an undescribed ON/OFF toggle (including `ON`/`OFF` commands with descriptions), and `Switch` items with described ON/OFF commands (so the descriptions can be shown).  
  - If only one command is available, it is sent directly when the menu item is selected.  
  - If two commands are available and the current state matches one of them, the other command is sent.  
  - In all other cases, a command selection menu is displayed.

- `switch/onoff/`  
  For `Switch` items without commands, with non-ON/OFF commands, or with undescribed ON/OFF commands; for `Dimmer` items without commands; and for non-`Switch` items whose available commands are exactly ON and OFF without descriptions. Displays an interactive toggle switch directly within the menu item.

- `switch/player/`  
  For Player items. Displays the PLAY or PAUSE state in the menu item and opens a full-screen control view with PLAY, PAUSE, NEXT, and PREVIOUS controls when selected.

- `switch/rollershutter/`  
  For Rollershutter items. Displays the current state in the menu item and opens a full-screen control view with UP, DOWN, and STOP controls.

See [Switch and Selection](https://next.openhab.org/docs/apps/garmin/#switch-and-selection) in the user manual for more information.

---

### Subfolder `text/`

Implements the Text widget, which displays the current state of an item without allowing user interaction.
If the Text element has no item, it is displayed as static text: only the label is shown and selecting it has no effect.

See also [Text](https://next.openhab.org/docs/apps/garmin/#text) in the user manual.
