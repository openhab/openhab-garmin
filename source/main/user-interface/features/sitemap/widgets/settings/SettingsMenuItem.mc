import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * This menu item implementation represents the settings menu entry.
 * On most button-based devices, the settings menu is a parallel menu to the
 * homepage menu, accessible by scrolling past the edges—see HomepageMenuDelegate
 * for details. On touch devices and on button-based Edge devices, however, the
 * settings menu appears as a regular menu item within the homepage menu, using
 * this menu item class.
 */

(:exclForSettingsFooter)
class SettingsMenuItem extends StructuredMenuItem {

    // Singleton accessor
    private static var _instance as SettingsMenuItem?;
    public static function get() as SettingsMenuItem {
        if( _instance == null ) {
            _instance = new SettingsMenuItem();
        }
        return _instance as SettingsMenuItem;
    }

    // Constructor
    private function initialize() {
        // To offset this menu item from the sitemap menu items
        // the font is colored in light grey
        StructuredMenuItem.initialize( { 
            :id => "__settings__",
            :label => Application.loadResource( Rez.Strings.Settings ) as String,
            :actionIcon => ACTION_ICON_PAGE
        } );
    }

    // Sets the color dynamically to accommodate
    // night mode and focus changes.
    public function onUpdate( dc as Dc ) {
        var theme = getCurrentTheme();
        setLabelColor( theme.textColor );
        setIcon( theme.menuSettings );
        StructuredMenuItem.onUpdate( dc );
    }

    // On select, show the settings
    public function onSelect() as Boolean {
        traceSelect( "settings" );
        SettingsMenuManager.showSettings( WatchUi.SLIDE_LEFT );
        return true;
    }

    /*
     * Debug-only E2E trace for the select outcome, matching the event every
     * other menu item emits. It keeps "a select reached an item" distinguishable
     * from "the press never arrived" for the last entry of the homepage menu.
     * Release builds contain no text and no call.
     */
    (:debug)
    private function traceSelect( result as String ) as Void {
        E2ETrace.selectionHandled( "Settings", result, ViewStack.getDepth() );
    }
    (:release)
    private function traceSelect( result as String ) as Void {}
}