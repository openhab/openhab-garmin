# Folder `themes`

Themes define the color schemes used in the app and can change dynamically at runtime. This also includes icons whose colors must be adapted to the active theme. Themes are used to implement the light and dark mode on Edge devices.

---

## Key Concepts

Themes use a slightly more technical approach than [device-specific configuration](../config/#folder-config).

For themes, the `Theme` interface declares only the available variables and does not define any default values. All values are provided by concrete theme implementations. This reflects the fact that themes usually differ across most, if not all, variables, making shared defaults less practical than in device-specific configuration.

These implementations may also use inheritance to derive variants from an existing theme, allowing shared styling definitions to be reused and selectively refined.

To support runtime theme changes, themes are accessed through the `ThemeManager` class. Multiple `ThemeManager` implementations are available, depending on the capabilities of the target device.

Each device is assigned a specific `ThemeManager` implementation via its source path configuration in the [`monkey.jungle`](../../README.md#root-folder-) build file. The corresponding theme definitions required by that manager are included in the same device-specific source path.

---

## Folder Structure

- `definitions/`  
  Contains the concrete theme definitions, including colors and icons.

  - `base/Theme.mc`  
    Declares the `Theme` interface that must be implemented by all themes. All theme variables are defined and documented here.

  - `base/BaseTheme.mc`  
    Provides a minimal base implementation with a small set of shared theme values.

  - `device-default/DefaultTheme.mc`  
    Defines the default dark theme used on devices that do not support switching between light and dark modes.

  - `device-night-mode/`  
    Contains theme implementations for devices that support light and dark mode, as well as dedicated focus themes.

    On button-based watches, menus scroll with each up or down button press and the focused item remains centered. On Edge devices, the focus can change without the visible menu items moving. In this case, a dedicated visual focus indicator is required.

    - `dark/`  
      The dark theme.

    - `light/`  
      The light theme.

    - `focus-blue/`  
      Used primarily in the simulator for button-based Edge x40 devices. These devices provide a native focus indicator on the physical device, but this is not reflected in the simulator. Therefore, the `focus-blue` theme adds a visible focus style for simulation purposes.

    - `focus-inverted/`  
      Used for button-based Edge x50 devices. These devices provide a native focus indicator that requires the menu item implementation to use contrasting colors for the focused item.

- `manager/`  
  Contains the `ThemeManager` implementations responsible for selecting and providing the active theme at runtime.

  - `device-default/ThemeManager.mc`  
    Implements the `ThemeManager` for devices that do not support light and dark mode switching.

  - `device-night-mode/base/BaseNightModeThemeManager.mc`  
    A base class for `ThemeManager` implementations on devices that support light and dark mode, such as Edge devices. Two concrete implementations build on this base class.

  - `device-night-mode/device-default/ThemeManager.mc`  
    Implementation for devices that do not require a focus indicator, typically touch-based devices such as the Edge 1040 or 1050.

  - `device-night-mode/device-focus-indicator/ThemeManager.mc`  
    Implementation for devices that require a focus indicator, typically button-based devices such as the Edge 540 or 550.

---

## Live Theme Changes

On devices that support night mode, the theme can change while the app is running.
`OHApp.onNightModeChanged()` updates the `NightModeTracker` and the `ThemeManager`
and requests a redraw, but it does not recreate views, menus, or drawables.
Therefore, nothing may cache a theme-dependent value beyond a single draw:

- Read colors and theme-dependent `ResourceId`s from `ThemeManager.current` when drawing,
  not when a class is loaded or an object is constructed.
- A `Drawable` that has to cache a theme-dependent resource (for example a `Bitmap`
  that is created in a constructor) must refresh it before drawing. The resource is only
  replaced if the theme actually changed, so nothing is recreated while it stays the same.
  See `OnOffStateDrawable`, `PlayPauseBitmap`, `PlayerMenuItemStateBitmap`,
  `BaseInputHint`, and `PlayerView` for this pattern.
- Menus are long-lived and are not rebuilt after a theme change. `BaseMenu` therefore
  applies the theme in `drawFooter()`, which is the draw callback of the `CustomMenu`
  base class. That is where the default footer logo and the menu background color
  are refreshed.

---

## Toggle Switch Bitmaps

The toggle switch bitmaps defined by the themes are expensive to allocate and stay in
memory for the lifetime of the app. `OnOffStateBitmaps` creates them while the theme is
constructed, and `getShared()`/`getSharedSmall()` share one instance between all
themes that use the same colors, so only one set is allocated per color combination.

The bitmaps must be allocated during theme construction. Delaying the allocation until
a toggle is first drawn has been observed to break text rendering on Edge x50 devices,
so the constructor calls `createBitmaps()`.

Because sharing is keyed on the colors that feed into the bitmaps (`onColor`,
`stateColor`, and `backgroundColor`), and not on the theme class, derived focus themes
reuse the bitmaps of the theme they are based on. Focus themes that only override
colors not used by the toggle switch therefore allocate nothing extra.

The focus themes themselves are created lazily by `ThemeManager.getFocusedTheme()`.
They must never be constructed while the `ThemeManager` class is being initialized:
an exception during class initialization cannot be caught and would also make
`ThemeManager.current` unavailable to the views. Devices that suppress the focus
indicator (`Config.UI_MENU_FOCUS_EDGEX50_SUPRESS_FOCUS_INDICATOR`) never create them
at all.

To keep the allocations out of the draw callbacks, the focus theme is created outside
of a draw: `BaseMenu` requests it when the menu is initialized, and the focus theme
`ThemeManager` recreates it in `update()` after a night mode change. In addition, the
bitmaps are cleared to transparent explicitly, because `Dc.clear()` otherwise fills
with the background color currently set on the drawing context, which would leave an
opaque white or black rectangle around the switch.

If a bitmap cannot be allocated, the getters return `null` and `OnOffStateDrawable`
draws the switch directly instead. See `drawWithoutBitmap()` in
`BufferedBitmapDrawable`.
