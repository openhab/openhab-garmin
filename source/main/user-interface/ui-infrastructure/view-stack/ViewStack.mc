import Toybox.Lang;
import Toybox.WatchUi;

/*
 * The `ViewStack` must be used throughout the app for switching views
 * or pushing and popping them from the stack.
 *
 * It maintains its own view stack, independent of the system API stack.
 * This allows the app to track how many views are currently active so that,
 * in case of an update or an error, all views can be removed and replaced
 * with the home view or an error view.
 *
 * The initial implementation only tracked the number of active views.
 * Due to bugs in the WatchUi.getCurrentView() implementation on some
 * newer Garmin devices, this was replaced with a fully managed
 * parallel view stack.
 *
 * For details about the underlying issue, see:
 * https://github.com/openhab/openhab-garmin/issues/215
 */
 class ViewStack {
    
    // Tuple used to store each view/delegate layer in the view stack
    typedef ViewInputPair as [ WatchUi.Views?, WatchUi.InputDelegates? ];

    // The view stack
    private static var _viewStack as Array<ViewInputPair> = [];

    // Returns the current view and delegate, replacing WatchUi.getCurrentView().
    // Replacing that API was the primary reason for introducing our own view stack. 
    // See the class-level comment above for details.
    public static function getCurrentView() as [ WatchUi.Views?, WatchUi.InputDelegates? ] {
        return _viewStack.size() > 0 
               ? _viewStack[_viewStack.size()-1]
               : [null, null];
    }
    
    // Pops a view from the stack, replacing WatchUi.popView().
    //
    // The base view is never popped. Leaving the app is done explicitly in
    // HomepageMenuDelegate.onBack(), so that a stray or duplicate back event
    // (or any other unintended pop) cannot discard the base view and with it
    // the app. Such a pop is reported as a non-fatal UI exception, which is
    // shown to the user as a toast.
    public static function popView( transition as SlideType ) as Void {
        if( _viewStack.size() <= 1 ) {
            throw new NonFatalUserInterfaceException( 
                NonFatalUserInterfaceException.EX_POP_VIEW_ON_EMPTY_STACK 
            );
        }
        WatchUi.popView( transition );
        _viewStack = _viewStack.slice( 0, _viewStack.size() - 1 );
        // Logger.debug( "ViewStack.popView: new stack size=" + _viewStack.size() );
    }

    // Removes the top view from the tracked stack without popping the system
    // view. Used for views that the system dismisses on its own, e.g. a
    // WatchUi.Confirmation after the user selected a response.
    public static function discardTopView() as Void {
        if( _viewStack.size() > 0 ) {
            _viewStack = _viewStack.slice( 0, _viewStack.size() - 1 );
            // Logger.debug( "ViewStack.discardTopView: new stack size=" + _viewStack.size() );
        }
    }

    // Pushes a view onto the stack, replacing WatchUi.pushView().
    public static function pushView( view as Views, delegate as InputDelegates?, transition as SlideType ) as Void {
        WatchUi.pushView( view, delegate, transition );
        _viewStack.add( [ view, delegate ] );
        // Logger.debug( "ViewStack.pushView: new stack size=" + _viewStack.size() );
    }

    // This function must be called in OHApp.getInitialView() to store the initial view.
    // The initial view only needs to be stored, since the API automatically
    // pushes it onto the stack when it is returned from getInitialView().
    public static function registerInitialView( view as Views, delegate as InputDelegates? ) as Void {
        // Safeguard against registerInitialView being called multiple times
        if( _viewStack.size() > 0 ) {
            _viewStack = [];
        }
        _viewStack.add( [ view, delegate ] );
        // Logger.debug( "ViewStack.registerInitialView: new stack size=" + _viewStack.size() );
    }

    // Removes all views from the stack except the base view
    // and replaces the base view with the specified view.
    public static function resetTo( view as Views, delegate as InputDelegates? ) as Void {
        // Logger.debug( "ViewStack.resetTo: previous stack size=" + _viewStack.size() );
        while( _viewStack.size() > 1 ) {
            popView( WatchUi.SLIDE_IMMEDIATE );
        }
        // Logger.debug( "ViewStack.resetTo: new stack size=" + _viewStack.size() );
        switchToView( view, delegate, WatchUi.SLIDE_BLINK );
    }

    // Switches the view on the top of the view stack, replaces ViewStack.switchToView()
    public static function switchToView( view as Views, delegate as InputDelegates?, transition as SlideType ) as Void {
        // Logger.debug( "ViewStack.switchToView: stack size=" + _viewStack.size() );
        WatchUi.switchToView( view, delegate, transition );
        // This function may also be called on an empty view stack,
        // and in that case we just add the new view to the view stack
        var size = _viewStack.size();
        var entry = [view, delegate];
        if( size > 0 ) {
            _viewStack[_viewStack.size()-1] = entry;
        } else {
            _viewStack.add( entry );
        }
    }

}
