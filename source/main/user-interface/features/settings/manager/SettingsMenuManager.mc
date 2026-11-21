import Toybox.Lang;
import Toybox.WatchUi;

/*
 * The settings menu handler is responsible for managing the settings section.
 * It provides functions to enter and leave the section as needed.
 *
 * Whether the settings are shown is always derived from the internal
 * `ViewStack`, never from a cached flag: a flag could diverge from the views
 * that are actually displayed (e.g. when the stack is reset by an update or
 * an error). The SDK's `WatchUi.getCurrentView()` must not be used for this,
 * because it is unreliable on some devices — see #215.
 *
 * Settings visibility is tracked as a `ViewStack.Scope`. Any view opened while
 * the settings scope is active inherits it, so future settings views (e.g.
 * submenus, pickers or dialogs) automatically belong to the settings section
 * without having to be registered here.
 */
class SettingsMenuManager {
    /*
    * Both the settings menu and its input delegate are maintained as singletons.
    * The specific delegate implementation depends on whether the device uses 
    * button-based or touch-based input.
    */
    private static var _settingsMenu as SettingsMenu = new SettingsMenu();
    (:exclForSettingsMenuItem)
    private static var _settingsMenuDelegate as ButtonSettingsMenuDelegate = new ButtonSettingsMenuDelegate();
    (:exclForSettingsFooter)
    private static var _settingsMenuDelegate as TouchSettingsMenuDelegate = new TouchSettingsMenuDelegate();

    // Returns true if the current view belongs to the settings section.
    // This includes the settings root and any view opened from it.
    public static function isShowingSettings() as Boolean {
        return ViewStack.getCurrentScope() == ViewStack.SETTINGS;
    }
    
    /*
    * Opens the settings menu.
    *
    * The `slideType` defines both the direction of the slide animation 
    * and which menu item should be focused when the settings menu appears.
    *
    * - `SLIDE_UP` and `SLIDE_DOWN` are used on button-based devices.
    * - `SLIDE_RIGHT` and `SLIDE_LEFT` are used on touch-based devices.
    */
    public static function showSettings( slide as SlideType ) as Void {
        if( isShowingSettings() ) {
            throw new GeneralException( "Called showSettings when the settings section was already shown" );
        }

        // Ensure that we were called from the HomepageMenu
        if( ! ( ViewStack.getCurrentView()[0] instanceof HomepageMenu ) ) {
            throw new GeneralException( "showSettings can only be called from a HomepageMenu" );
        }

        // Depending on the slide type we decide which
        // settings menu item to focus
        if( slide == WatchUi.SLIDE_UP ) {
            _settingsMenu.focusFirst();
        } else if( slide == WatchUi.SLIDE_DOWN ){
            _settingsMenu.focusLast();
        } else if( slide == WatchUi.SLIDE_LEFT ){
            // do nothing, the menu will open either on the first
            // or if previously opened to whichever item was focused last
        } else {
            throw new GeneralException( "showSettings only supports SLIDE_UP, SLIDE_DOWN or SLIDE_LEFT" );
        }
        // Push the view on the view stack, entering the settings scope
        ViewStack.pushViewInScope(
            _settingsMenu,
            _settingsMenuDelegate,
            slide,
            ViewStack.SETTINGS
        );
    }
    
    // Hide the settings
    // Again, the slide type also decides on the focus of the Homepage menu
    // we are returning to.
    //
    // This function only leaves the settings section from the settings root.
    // Any future settings subview is popped by its own delegate instead.
    public static function hideSettings( slide as SlideType ) as Void {
        if( ! isShowingSettings() ) {
            throw new GeneralException( "Called hideSettings when settings were not shown" );
        }
        if( ! ( ViewStack.getCurrentView()[0] instanceof SettingsMenu ) ) {
            throw new GeneralException( "hideSettings can only be called from the settings root" );
        }

        // Depending on the slide type we decide which
        // homepage menu item to focus
        if( slide == WatchUi.SLIDE_UP ) {
            HomepageMenu.get().focusFirst();
        } else if( slide == WatchUi.SLIDE_DOWN ){
            HomepageMenu.get().focusLast();
        } else if( slide == WatchUi.SLIDE_RIGHT ){
            // do nothing, the menu will open either on the first
            // or if previously opened to whichever item was focused last
        } else {
            throw new GeneralException( "hideSettings only supports SLIDE_UP, SLIDE_DOWN or SLIDE_RIGHT" );
        }
        // Pop the view from the view stack, leaving the settings scope
        ViewStack.popView( slide );
    }
}