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

    /*
     * The scope identifies the part of the app a view belongs to.
     *
     * Views pushed while another view is shown inherit its scope. This way,
     * any view opened from within a section (e.g. future settings submenus,
     * pickers or dialogs) remains part of that section without having to
     * register its concrete type. Popping a view restores the scope of the
     * view below it.
     */
    public enum Scope {
        MAIN,       // Homepage, sitemap pages, loading and error views
        SETTINGS    // The settings root and all views opened from it
    }

    // Tuple used to store each view/delegate layer in the view stack
    typedef ViewInputPair as [ WatchUi.Views?, WatchUi.InputDelegates?, Scope ];

    // The view stack
    private static var _viewStack as Array<ViewInputPair> = [];

    // Returns the current view, delegate and scope, replacing WatchUi.getCurrentView().
    // Replacing that API was the primary reason for introducing our own view stack. 
    // See the class-level comment above for details.
    public static function getCurrentView() as ViewInputPair {
        return _viewStack.size() > 0 
               ? _viewStack[_viewStack.size()-1]
               : [null, null, MAIN];
    }

    // Returns the scope of the current view. Derived from the tracked view
    // stack, so it cannot diverge from the views that are actually shown.
    public static function getCurrentScope() as Scope {
        return getCurrentView()[2];
    }

    // Returns the number of views on the tracked stack.
    public static function getDepth() as Number {
        return _viewStack.size();
    }

    // Returns true if the given view is currently part of the tracked stack.
    // Used to detect pages that became empty while they are still part of the
    // current navigation path.
    public static function contains( view as WatchUi.Views ) as Boolean {
        for( var i = 0; i < _viewStack.size(); i++ ) {
            var entryView = _viewStack[i][0];
            if( entryView != null && ( entryView as WatchUi.Views ).equals( view ) ) {
                return true;
            }
        }
        return false;
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
        traceCurrentView();
    }

    // Removes the top view from the tracked stack without popping the system
    // view. Used for views that the system dismisses on its own, e.g. a
    // WatchUi.Confirmation after the user selected a response.
    public static function discardTopView() as Void {
        if( _viewStack.size() > 0 ) {
            _viewStack = _viewStack.slice( 0, _viewStack.size() - 1 );
            // Logger.debug( "ViewStack.discardTopView: new stack size=" + _viewStack.size() );
            traceCurrentView();
        }
    }

    // Pushes a view onto the stack, replacing WatchUi.pushView().
    // The new view inherits the scope of the current view.
    public static function pushView( view as Views, delegate as InputDelegates?, transition as SlideType ) as Void {
        pushViewInScope( view, delegate, transition, getCurrentScope() );
    }

    // Pushes a view onto the stack with an explicit scope.
    // Used when entering a section, e.g. the settings.
    public static function pushViewInScope(
        view as Views,
        delegate as InputDelegates?,
        transition as SlideType,
        scope as Scope
    ) as Void {
        WatchUi.pushView( view, delegate, transition );
        _viewStack.add( [ view, delegate, scope ] );
        // Logger.debug( "ViewStack.pushView: new stack size=" + _viewStack.size() );
        traceCurrentView();
    }

    // This function must be called in OHApp.getInitialView() to store the initial view.
    // The initial view only needs to be stored, since the API automatically
    // pushes it onto the stack when it is returned from getInitialView().
    //
    // The initial view is always registered in the MAIN scope. Because it is
    // the base entry, this establishes the invariant that resetTo() relies on.
    public static function registerInitialView( view as Views, delegate as InputDelegates? ) as Void {
        // Safeguard against registerInitialView being called multiple times
        if( _viewStack.size() > 0 ) {
            _viewStack = [];
        }
        _viewStack.add( [ view, delegate, MAIN ] );
        // Logger.debug( "ViewStack.registerInitialView: new stack size=" + _viewStack.size() );
        traceCurrentView();
    }

    // Removes all views from the stack except the base view
    // and replaces the base view with the specified view.
    //
    // The resulting view is always in the MAIN scope. This is not set
    // explicitly here: it relies on the base entry being MAIN, as established
    // by registerInitialView(), and on switchToView() below inheriting the
    // scope of the entry it replaces. The base entry must therefore always
    // remain in MAIN, otherwise resetTo() would carry another scope over to
    // the view it installs.
    public static function resetTo( view as Views, delegate as InputDelegates? ) as Void {
        // Logger.debug( "ViewStack.resetTo: previous stack size=" + _viewStack.size() );
        while( _viewStack.size() > 1 ) {
            popView( WatchUi.SLIDE_IMMEDIATE );
        }
        // Logger.debug( "ViewStack.resetTo: new stack size=" + _viewStack.size() );
        switchToView( view, delegate, WatchUi.SLIDE_BLINK );
    }

    // Switches the view on the top of the view stack, replaces ViewStack.switchToView()
    // The new view inherits the scope of the view it replaces.
    // This includes the base view, which is how resetTo() keeps the resulting
    // view in the MAIN scope.
    public static function switchToView( view as Views, delegate as InputDelegates?, transition as SlideType ) as Void {
        // Logger.debug( "ViewStack.switchToView: stack size=" + _viewStack.size() );
        var scope = getCurrentScope();
        WatchUi.switchToView( view, delegate, transition );
        // This function may also be called on an empty view stack,
        // and in that case we just add the new view to the view stack
        var size = _viewStack.size();
        var entry = [view, delegate, scope];
        if( size > 0 ) {
            _viewStack[_viewStack.size()-1] = entry;
        } else {
            _viewStack.add( entry );
        }
        traceCurrentView();
    }

    /*
    * Emits an E2E view event for the view on top of the tracked stack.
    *
    * Debug-only: the release implementation is empty, so neither the view
    * classification nor any event text is present in release builds.
    */
    (:debug)
    private static function traceCurrentView() as Void {
        var current = getCurrentView();
        var view = current[0];
        if( view == null ) {
            return;
        }
        var typedView = view as WatchUi.Views;
        var category = current[2] == SETTINGS
            ? "settings"
            : ( typedView instanceof ErrorView ? "error" : "sitemap" );
        E2ETrace.viewShown( category, viewName( typedView ), _viewStack.size() );
    }
    (:release)
    private static function traceCurrentView() as Void {}

    // Stable view names for E2E assertions.
    (:debug)
    private static function viewName( view as WatchUi.Views ) as String {
        if( view instanceof ErrorView ) { return "ErrorView"; }
        if( view instanceof LoadingView ) { return "LoadingView"; }
        if( view instanceof HomepageMenu ) { return "HomepageMenu"; }
        if( view instanceof PageMenu ) { return "PageMenu"; }
        if( view instanceof SettingsMenu ) { return "SettingsMenu"; }
        // The remaining BaseMenu implementation is CommandMenu. It is excluded
        // on devices that show a WatchUi.ActionMenu instead, so it is matched
        // through the base class rather than by name.
        if( view instanceof BaseMenu ) { return "CommandMenu"; }
        if( view instanceof DynamicPicker ) { return "DynamicPicker"; }
        if( view instanceof PlayerView ) { return "PlayerView"; }
        if( view instanceof RollershutterView ) { return "RollershutterView"; }
        if( view instanceof ControlView ) { return "ControlView"; }
        if( view instanceof WatchUi.Confirmation ) { return "Confirmation"; }
        return "View";
    }

}
