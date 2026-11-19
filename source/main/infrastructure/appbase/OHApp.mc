import Toybox.Application;
import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Timer;

/*
 * Base class for the app. Responsible for initializing
 * the initial view for both glance and widget modes, as well
 * as handling logic that runs during app startup and shutdown.
 *
 * It also implements the active/inactive lifecycle that devices with
 * API level 4.2.3 and later use when the app is hidden by the system
 * instead of being terminated (e.g. when the watch face is shown).
 *
 * This class also includes logic executed when the app is updated
 * or when settings are changed.
 */
(:glance)
class OHApp extends Application.AppBase {

    // In some places we have different logic depending on
    // whether we are in the glance or widget
    private static var _glanceView as GlanceSitemapView?;
    public static function isGlance() as Boolean {
        return _glanceView != null;
    }

    // Constructor
    public function initialize() {
        AppBase.initialize();
    }

    // onStart() is called on application start up
    public function onStart(state as Dictionary?) as Void {
        // this could be used for startup logic that is common to
        // glance and widget. Currently there is none.
        //
        // If state contains :resume, the app was terminated while it was
        // suspended. In that case the app starts fresh: the sitemap is
        // restored from storage and the request loop is started by
        // getInitialView(), so there is nothing to restore here.
    }

    // onActive() is called when the app becomes visible again.
    // Only invoked on devices with the active/inactive lifecycle (API level
    // 4.2.3+); on older devices the app is terminated instead of hidden.
    (:typecheck(disableGlanceCheck))
    public function onActive(state as Dictionary?) as Void {
        // Logger.debug( "OHApp.onActive" );

        // The glance process is not affected by the foreground lifecycle
        if( isGlance() ) {
            return;
        }

        AppLifecycle.setActive();

        // Continue the request loop that was paused in onInactive().
        // If the app is started for the first time, getInitialView() starts
        // the loop and this is a no-op.
        SitemapRequest.get().resume();

        // Redraw the current view, since data may have changed while
        // updates were suspended
        if( ViewStack.getCurrentView()[0] != null ) {
            WatchUi.requestUpdate();
        }
    }

    // onInactive() is called when the app is hidden by the system, e.g. when
    // the watch face is shown after inactivity. The app keeps running in this
    // state, but access to system resources is limited. Polling and
    // asynchronous sitemap processing are therefore suspended until the app
    // becomes visible again.
    (:typecheck(disableGlanceCheck))
    public function onInactive(state as Dictionary?) as Void {
        // Logger.debug( "OHApp.onInactive" );

        // The glance process is not affected by the foreground lifecycle
        if( isGlance() ) {
            return;
        }

        AppLifecycle.setInactive();
        SitemapRequest.get().pause();
    }

    // onStop() is called when the application is exiting.
    // This is also the case when the app is suspended (state :suspend),
    // i.e. terminated by the system to free resources while it was hidden.
    (:typecheck(disableGlanceCheck))
    public function onStop(state as Dictionary?) as Void {
        // Logger.debug( "OHApp.onStop" );
        if( ! isGlance() ) {
            try {
                // Cancel in-flight web requests and stop the request loop before
                // the app terminates, so that no timer or communications callback
                // runs while the app is being torn down.
                BaseRequest.cancelAllRequests();
                SitemapRequest.get().stop();
                // A command response restarts the sitemap request (see
                // BaseCommandRequest.onReceive), so cancel once more to catch a
                // request that was created by the callback above.
                BaseRequest.cancelAllRequests();

                SitemapStore.persist();
            } catch( ex ) {
                // Persisting the sitemap must never crash the app on shutdown
                // Logger.debug( "OHApp.onStop: persisting sitemap failed" );
            }
        }
    }

    // Initializes the widget view
    (:typecheck(disableGlanceCheck))
    public function getInitialView() as [Views] or [Views, InputDelegates] {
        try {
            // Logger.debug( "OHApp.getInitialView" );
            // First we initialize the menu from storage
            var menu = HomepageMenu.createFromStorage();
            var hasMenu = menu != null;

            // Then we start the sitemap request
            SitemapRequest.get().start();
            
            // Starting the sitemap request may return an immediate error, which
            // SitemapRequest.onReceive reports to the ExceptionHandler via
            // handleException, and the ExceptionHandler stores it
            // consumeStartupException then acts on the exception, and based if toasts
            // shall be used displays a toast for non-fatal errors, and in all
            // other cases returns an errorView for display.
            var errorView = ExceptionHandler.consumeStartupException( hasMenu );
            
            var view;
            var delegate = null;

            if( errorView != null ) {
                // If there is an error view, display it
                view = errorView;
            } else if( menu != null ) {
                // If there is HomepageMenu, display it
                view = menu;
                delegate = HomepageMenuDelegate.get();
            } else {
                // Otherwise show the loading view
                view = new LoadingView( false );
            }

            // For the reasons outlined in the ViewStack inline documentation,
            // we maintain our own view stack and therefore need to
            // register the initial view.
            ViewStack.registerInitialView( view, delegate );

            return delegate != null
                   ? [view, delegate]
                   : [view];

        } catch( ex ) {
            // Any exceptions occuring in this function are 
            // also displayed as error view
            var errorView = ErrorView.createOrUpdate( ex );
            ViewStack.registerInitialView( errorView, null );
            return [ ErrorView.createOrUpdate( ex ) ];
        }
    }

    
    // Initializes the glance view
    function getGlanceView() as [ GlanceView ] or [ GlanceView, GlanceViewDelegate ] or Null {
        // Logger.debug( "OHApp.getGlanceView" );
        _glanceView = new GlanceSitemapView();
        return [ _glanceView ];
    }

    /*
    function getGlanceTheme() as AppBase.GlanceTheme {
        // Logger.debug( "OHApp.getGlanceTheme" );
        return GLANCE_THEME_BLUE;
    }
    */

    // Define the sync delegate, which in our case is used
    // to request a sitemap or send a command via WiFi.
    (:typecheck(disableGlanceCheck))
    function getSyncDelegate() as $.Toybox.Communications.SyncDelegate or Null {
        // Logger.debug( "OHApp.getSyncDelegate" );

        var syncDelegate = BaseSyncDelegate.getCurrentSyncDelegate();
        
        if( syncDelegate != null ) {
            // Logger.debug( "OHApp.getSyncDelegate: returning current sync delegate" );
            return syncDelegate;
        } else {
            // Logger.debug( "OHApp.getSyncDelegate: no current sync delegate, defaulting to sitemap sync delegate" );
            return SitemapSyncDelegate.get();
        }
    }
    
    // If there is a new app version or app settings
    // have changed, we clear the storage to avoid
    // using data that may not apply anymore or
    // is structured differently than a new app version
    // expecteds.
    (:release) function onAppUpdate() as Void {
        Storage.clearValues();
    }
    function onSettingsChanged() as Void {
        // Logger.debug( "OHApp.onSettingsChanged" );
        Storage.clearValues();
    }

    // Switching into night mode may change the screen colors
    (:typecheck(disableGlanceCheck))
    public function onNightModeChanged() as Void {
        // Logger.debug( "OHApp.onNightModeChanged" );
        
        // The ThemeManager exists only in the foreground scope.
        // In the glance scope, theme handling is very simple and
        // implemented directly in the onUpdate() of the glance view.
        if( ! isGlance() ) {
            NightModeTracker.get().update();
            ThemeManager.update();
        }
        
        WatchUi.requestUpdate();
    }
}