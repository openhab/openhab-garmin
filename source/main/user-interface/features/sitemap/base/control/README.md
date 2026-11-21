# Folder `control`

Provides the foundation for full-screen control views that support more advanced interaction models than can be implemented directly within menu items.

These views are used for sitemap elements that require more complex input handling, such as selecting numeric values for Setpoint or Slider items, or controlling media playback. They enable richer interaction patterns while remaining aligned with the input capabilities of the respective device.

On button-based devices, full-screen views render visual input hints that indicate the function assigned to each physical button. On button-based devices with additional touch support, these input hints also respond to touch interaction. On purely touch-based devices, dedicated on-screen buttons are displayed instead.

For examples, see the screenshots in the [Setpoint/Slider section](https://next.openhab.org/docs/apps/garmin/#setpoint-and-slider).

The base classes in this folder provide the underlying mechanisms for:

- Laying out drawables  
- Rendering and managing input hints  
- Routing and processing user input  

---

## Folder Structure

### Subfolder `input-hints/`

Contains classes responsible for rendering input hints. These visual elements map physical device buttons to specific actions within a control view.

### Subfolder `touch-area/`

Provides abstractions for defining circular and rectangular touch areas that trigger actions. These integrate with `ControlView` to enable touch-based interaction.

### Subfolder `view/`

Defines the `ControlView` base class and the associated input delegate used by all full-screen control implementations.
