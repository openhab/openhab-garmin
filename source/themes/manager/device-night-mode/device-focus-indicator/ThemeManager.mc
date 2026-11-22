import Toybox.Lang;
import Toybox.Graphics;

/*
 * The ThemeManager keeps track of the current theme.
 * On devices that support multiple themes, it also
 * manages switching between them.
 *
 * This implementation of the base night mode theme manager
 * adds switching of a dedicated focus theme to provide
 * different styling for the currently focused menu item.
 *
 * It is used on button-based devices, which require a
 * visual indicator of the focused menu item when scrolling
 * through the menu using buttons. This also applies to
 * devices that provide both control buttons and a touch
 * screen, such as the Edge 840 and 850.
 */
class ThemeManager extends BaseNightModeThemeManager {
    
    // The focus themes are created on first use and then reused.
    //
    // They are not created at all if the Edge x50 focus indicator workaround
    // is active, in which case focused menu items use the current theme.
    // This also avoids allocating their toggle bitmaps.
    private static var _darkFocus as Theme?;
    private static var _lightFocus as Theme?;

    // The focus theme currently in use.
    // Created on first use and dropped by update() when the theme changes.
    private static var _focused as Theme?;

    /*
     * Returns the theme to use for focused menu items.
     *
     * The focus theme is created lazily on first use. It must not be created
     * while this class is being initialized: constructing a theme can fail,
     * and an exception during class initialization cannot be caught. It would
     * also make ThemeManager.current unavailable, which would leave fallback
     * views such as the error view for missing settings blank.
     */
    public static function getFocusedTheme() as Theme {
        var theme = _focused;
        if( theme == null ) {
            theme = resolveFocusedTheme();
            _focused = theme;
        }
        return theme as Theme;
    }

    // Determines the focus theme from the current configuration and night mode
    private static function resolveFocusedTheme() as Theme {
        // If the Edge x50 focus indicator workaround is active,
        // the focused menu item is rendered the same way as other menu items.
        // Otherwise, the focus theme is applied.
        // See the Config class for details about the Edge x50 devices.
        if( Config.UI_MENU_FOCUS_EDGEX50_SUPRESS_FOCUS_INDICATOR ) {
            return BaseNightModeThemeManager.current;
        }
        return NightModeTracker.get().isNightModeEnabled()
               ? getDarkFocus()
               : getLightFocus();
    }

    // Returns the focus theme for the dark theme, creating it if necessary
    private static function getDarkFocus() as Theme {
        if( _darkFocus == null ) {
            _darkFocus = new DarkThemeFocus();
        }
        return _darkFocus as Theme;
    }

    // Returns the focus theme for the light theme, creating it if necessary
    private static function getLightFocus() as Theme {
        if( _lightFocus == null ) {
            _lightFocus = new LightThemeFocus();
        }
        return _lightFocus as Theme;
    }

    // Public function to update the current theme
    // Updates the base class and drops the cached focus theme, which is
    // recreated on the next use because it depends on the night mode.
    public static function update() as Void {
        BaseNightModeThemeManager.update();
        _focused = null;
    }

    // Constructor
    // Declared as private to prevent instantiation
    private function initialize() {
        BaseNightModeThemeManager.initialize();
    }

}