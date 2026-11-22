import Toybox.Lang;
import Toybox.Graphics;

/*
 * The ThemeManager keeps track of the current theme.
 * On devices that support multiple themes, it also
 * manages switching between them.
 *
 * This implementation of the base night mode theme manager
 * does not add additional functionality. It is used on
 * devices that do not require a focus indicator,
 * such as touch-based Edge devices.
 */
class ThemeManager extends BaseNightModeThemeManager {
    
    // Constructor
    // Declared as private to prevent instantiation
    private function initialize() {
        BaseNightModeThemeManager.initialize();
    }

    // Returns the theme to use for focused menu items.
    // Technically, compilation works without overriding this, by just inheriting
    // getFocusedTheme(). However that is a glitch in the compiler and causes
    // issues at runtime, because the inherited function cannot resolve
    // BaseNightModeThemeManager.current. See also update() below.
    public static function getFocusedTheme() as Theme {
        return BaseNightModeThemeManager.getFocusedTheme();
    }

    // Public function to update the current theme
    // Technically, compilation works without overriding
    // this, by just inheriting update(). However that
    // is a glitch in the compiler and causes issues at
    // runtime.
    public static function update() as Void {
        BaseNightModeThemeManager.update();
    }
}