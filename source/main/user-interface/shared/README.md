# Folder `shared`

Contains reusable classes that are shared across UI components. The code in this folder provides common building blocks and abstractions that simplify and standardize UI implementation throughout the application.

---

## Subfolder `connection/`

The `ConnectionModeIndicator` renders the traffic light–style connection status indicator, along with icons that show which connection channels are currently available.

For screenshots and additional details, see the [Network Access](https://next.openhab.org/docs/apps/garmin/#network-access) section of the user manual.

---

## Subfolder `drawables/`

Contains classes that represent and enhance drawable objects from the Garmin SDK. These abstractions extend the SDK’s functionality, particularly to improve positioning flexibility and maintain compatibility with older Connect IQ versions.

- `BufferedBitmapDrawable`, `BufferedBitmapFactory`, and `LegacyBufferedBitmap`  
  Provide workarounds for limitations in the Garmin SDK prior to CIQ 4.0.0, where `BufferedBitmap` did not offer sufficient support for custom positioning logic.

- `CachedText`, `CachedTextArea`, `TextCache`, and `TextBitmapPool`  
  Cache the rendered content of text drawables in `BufferedBitmap`s and blit it on subsequent draws. Rendering text is the dominant cost when drawing menu items, especially on touch devices where all visible items are redrawn on every frame of a scroll gesture. The number of cached bitmaps is limited by `Config.UI_TEXT_CACHE_SIZE` (0 disables the cache). The two drawable classes keep the interface of the SDK classes they extend, so they can be used in place of `Text` and `TextArea`.

- `RelativeText` and `InteractiveRelativeBitmap`  
  Extend the SDK’s `Text` and `Bitmap` classes to support proportional positioning relative to screen dimensions. While the SDK natively supports only basic centering, these classes allow placement based on percentages, for example positioning an element at 30 percent of the screen height.  
  `InteractiveRelativeBitmap` also defines a dedicated touch area and is used in control views to enable user interaction.

---

## Subfolder `utils/`

Contains general-purpose utility classes used throughout the UI:

- `CustomMath`  
  Provides commonly used mathematical helper functions.

- `LittleHelpers`  
  Contains miscellaneous helper functions that do not naturally belong to another module.

- `TextDimensions`  
  Allows calculating the width of a text string without requiring a `Dc` drawing context. This is useful when layout calculations must be performed before rendering is possible.

- `TimeFormatting`  
  Provides helper functions for formatting time and date values for display purposes.
