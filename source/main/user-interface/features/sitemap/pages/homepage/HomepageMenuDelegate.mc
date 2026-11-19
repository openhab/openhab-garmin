import Toybox.WatchUi;
import Toybox.System;
import Toybox.Lang;

/*
 * `HomepageMenuDelegate` is a subclass of `PageMenuDelegate` that provides 
 * additional input handling specific to the homepage on button-based devices, 
 * such as accessing the settings menu.
 *
 * As it is only needed once, it is implemented as a singleton.
 */
class HomepageMenuDelegate extends PageMenuDelegate {
    
    /******* STATIC *******/ 
    
    // Singleton accessor
    private static var _instance as HomepageMenuDelegate?;
    public static function get() as HomepageMenuDelegate {
        if( _instance == null ) {
            _instance = new HomepageMenuDelegate();
        }
        return _instance as HomepageMenuDelegate;
    }

    /******* INSTANCE *******/ 

    // Constructor
    private function initialize() {
        PageMenuDelegate.initialize();
    }

    /*
    * The homepage is the base view of the widget, so pressing back leaves the app.
    * This is done explicitly here, because ViewStack.popView() never pops below
    * the base view.
    */
    public function onBack() as Void {
        System.exit();
    }

    /*
    * On button-based devices, event handlers trigger the opening of the settings menu.
    * The `slideType` indicates which settings menu item should be focused.
    *
    * - Scrolling down past the last item triggers `onNextPage()`: the settings menu slides in from the bottom, 
    *   and the first settings item is focused.
    * - Scrolling up past the first item triggers `onPreviousPage()`: the settings menu slides in from the top, 
    *   and the last settings item is focused.
    */
    (:exclForSettingsMenuItem)
    public function onNextPage() as Boolean {
        // Logger.debug( "HomepageMenuDelegate.onNextPage" );
        try {
            SettingsMenuManager.showSettings( WatchUi.SLIDE_UP );
        } catch( ex ) {
            ExceptionHandler.handleUserInterfaceException( ex );
        }
        return true;
    }
    (:exclForSettingsMenuItem)
    public function onPreviousPage() as Boolean {
        // Logger.debug( "HomepageMenuDelegate.onPreviousPage" );
        try {
            SettingsMenuManager.showSettings( WatchUi.SLIDE_DOWN );
        } catch( ex ) {
            ExceptionHandler.handleUserInterfaceException( ex );
        }
        return true;
    }
    // Button-based devices may also have touch screens. 
    // For those, tapping the footer should behave the same as scrolling down.
    (:exclForSettingsMenuItem)
    public function onFooter() as Void {
        // Logger.debug( "HomepageMenuDelegate.onNextPage" );
        onNextPage();
    }

    /*
    * `onPreviousPage()` and `onNextPage()` are only available on devices running CIQ 5.1.0 or later.
    * For older versions, equivalent behavior can be implemented using `onWrap()` for button-based scrolling.
    *
    * However, since button-based devices may also have touch screens, 
    * using `onPreviousPage()` and `onNextPage()` is preferred when supported,
    * as they also work with swipe gestures.
    */
    (:exclForCiq510Plus :exclForSettingsMenuItem)
    public function onWrap( key as Key ) as Boolean {
        // Logger.debug( "HomepageMenuDelegate.onWrap" );
        try {
            if( key == KEY_DOWN ) {
                onNextPage();
            } else if( key == KEY_UP ) {
                onPreviousPage();
            }
        } catch( ex ) {
            // Logger.debug( "PageMenuDelegate.onWrap: exception" );
            ExceptionHandler.handleUserInterfaceException( ex );
        }
        return false;
    }

}