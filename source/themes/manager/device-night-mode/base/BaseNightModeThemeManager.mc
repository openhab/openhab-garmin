import Toybox.Lang;
import Toybox.Graphics;

/*
 * The ThemeManager keeps track of the current theme.
 * On devices that support multiple themes, it also handles
 * switching between them.
 *
 * This base implementation for devices with night mode
 * support switches between the light theme (standard)
 * and the dark theme (night mode).
 */
class BaseNightModeThemeManager {

    // Themes are instantiated here and reused
    public static var dark as Theme = new DarkTheme();
    public static var light as Theme = new LightTheme();

    // Override the current theme
    public static var current as Theme = getCurrent();

    // Internal function to determine the current theme
    // Used when defining the member above and in onUpdate below
    private static function getCurrent() as Theme {
        return NightModeTracker.get().isNightModeEnabled() ? dark : light;
    }

    /*
     * Returns the theme to use for focused menu items.
     *
     * This implementation does not provide a dedicated focus theme, so
     * focused items are rendered with the current theme. Implementations
     * for devices with a focus indicator override this function.
     *
     * This is deliberately a function and not a static variable: focus
     * themes must never be constructed while the ThemeManager class is
     * being initialized, because an exception there would also make
     * ThemeManager.current unavailable to the fallback views.
     */
    public static function getFocusedTheme() as Theme {
        return current;
    }

    // Public function that updates the current theme
    public static function update() as Void {
        current = getCurrent();
    }

    // Constructor
    // Declared as protected.
    // These classes are not intended to be instantiated directly.
    // The base class constructor is protected so that subclasses
    // can invoke it. The constructors of the concrete subclasses
    // are private to prevent external instantiation.
    protected function initialize() {}

}