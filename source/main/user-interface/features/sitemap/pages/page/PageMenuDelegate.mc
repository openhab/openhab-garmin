import Toybox.WatchUi;
import Toybox.System;
import Toybox.Lang;

/*
 * `PageMenuDelegate` serves as the input delegate for `PageMenu` views.
 * Since it requires no context, it is implemented as a singleton and shared 
 * across all `PageMenu` instances.
 */
class PageMenuDelegate extends WatchUi.Menu2InputDelegate {
    
    /******* STATIC *******/ 

    // Singleton accessor
    private static var _instance as PageMenuDelegate?;
    public static function get() as PageMenuDelegate {
        if( _instance == null ) {
            _instance = new PageMenuDelegate();
        }
        return _instance as PageMenuDelegate;
    }

    /******* INSTANCE *******/ 

    // Needs to be public despite this being a singleton class,
    // due to a Monkey C quirk:
    // With protected, the static get() cannot access it
    // With private, the derived HomepageMenuDelegate cannot access
    public function initialize() {
        Menu2InputDelegate.initialize();
    }
    
    // onSelect() will call the event handler of the menu item,
    // and any exceptions will be passed on to the ExceptionHandler
    public function onSelect( item as WatchUi.MenuItem ) as Void {
        // Logger.debug( "PageMenuDelegate: onSelect" );
        traceInput( "select" );
        try {
            ( item as StructuredMenuItem ).onSelect();
        } catch( ex ) {
            // Logger.debug( "PageMenuDelegate.onSelect: exception" );
            ExceptionHandler.handleUserInterfaceException( ex );
        }
    }

    // Override onBack() to use our own popView() implementation
    public function onBack() as Void {
        // Logger.debug( "PageMenuDelegate.onBack" );
        traceInput( "back" );
        try {
            ViewStack.popView( WatchUi.SLIDE_RIGHT );
        } catch( ex ) {
            // Logger.debug( "PageMenuDelegate.onBack: exception" );
            ExceptionHandler.handleUserInterfaceException( ex );
        }
    }

    /*
    * Debug-only E2E trace for an input event that reached this delegate.
    *
    * It proves the simulator delivered the press to the shown view, which a
    * screenshot cannot: a press that arrives and does nothing and a press that
    * never arrives look identical on screen. The release implementation is
    * empty, so no view name or counter is computed in release builds.
    */
    (:debug)
    protected function traceInput( button as String ) as Void {
        E2ETrace.inputDelivered( button, ViewStack.currentViewName(), ViewStack.getDepth() );
    }
    (:release)
    protected function traceInput( button as String ) as Void {}
}