import Toybox.Application;
import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Timer;

/*
 * Base class for the app. Responsible for initializing
 * the initial view for both glance and widget modes, as well
 * as handling logic that runs during app startup and shutdown.
 *
 * It reports the active/inactive lifecycle that devices with API level 4.2.3
 * and later use when the app is hidden by the system instead of being
 * terminated (e.g. when the watch face is shown), as well as whether the
 * process runs as a glance. Both are tracked by `AppLifecycle`.
 *
 * This file also includes logic executed when the app is updated
 * or when settings are changed.
 */
(:glance)
class OHApp extends Application.AppBase {

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

        // Defensive: a glance is a separate process with its own lifecycle
        // (onStart/getGlanceView/onStop) and never receives active/inactive
        // callbacks.
        if( AppLifecycle.isGlance() ) {
            return;
        }

        AppLifecycle.setActive();

        try {
            // Display an error that was deferred while the app was hidden.
            // Done before resuming so that a newer error detected by the
            // resumed request is not overwritten by the deferred one.
            ExceptionHandler.showPendingException();

            // States captured before the app was hidden are only shown if they are
            // still within the expiration time. Otherwise they stay hidden until
            // the resumed request delivers data.
            if( HomepageMenu.exists() ) {
                HomepageMenu.get().hideStatesIfStale();
            }

            // Continue the request loop that was paused in onInactive().
            // If the app is started for the first time, getInitialView() starts
            // the loop and this is a no-op.
            SitemapRequest.get().resume();
        } catch( ex ) {
            // An exception must not escape a lifecycle callback, as it would
            // terminate the app. It is reported by the regular error handling.
            ExceptionHandler.handleBackgroundException( ex );
        }

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

        // Defensive: a glance is a separate process with its own lifecycle
        // (onStart/getGlanceView/onStop) and never receives active/inactive
        // callbacks.
        if( AppLifecycle.isGlance() ) {
            return;
        }

        AppLifecycle.setInactive();

        try {
            SitemapRequest.get().pause();
        } catch( ex ) {
            // An exception must not escape a lifecycle callback, as it would
            // terminate the app. It is reported by the regular error handling.
            ExceptionHandler.handleBackgroundException( ex );
        }
    }

    // onStop() is called when the application is exiting.
    // This is also the case when the app is suspended (state :suspend),
    // i.e. terminated by the system to free resources while it was hidden.
    (:typecheck(disableGlanceCheck))
    public function onStop(state as Dictionary?) as Void {
        // Logger.debug( "OHApp.onStop" );
        if( ! AppLifecycle.isGlance() ) {
            try {
                // Latch the request loop off before cancelling, so that a
                // command response cannot restart it during teardown.
                SitemapRequest.get().shutdown();
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
            // shall be used displays a toast for errors that do not require a
            // full-screen error, and in all other cases returns an errorView for display.
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
            return [ errorView ];
        }
    }

    
    // Initializes the glance view
    function getGlanceView() as [ GlanceView ] or [ GlanceView, GlanceViewDelegate ] or Null {
        // Logger.debug( "OHApp.getGlanceView" );
        AppLifecycle.setGlance();
        return [ new GlanceSitemapView() ];
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

    /*
     * Settings changes cannot be applied to a running app: Connect IQ has no
     * API to restart the app, and the singletons have already cached the
     * previous URL, sitemap name, authentication data, polling interval and
     * sitemap.
     *
     * This callback may also be delivered to an app instance that was created
     * only to receive it, i.e. before the initial view exists. Such an instance
     * has not read any settings or sitemap data yet, so it discards the
     * persisted data and starts normally with the new settings when
     * getInitialView() runs.
     *
     * Otherwise the app stops polling, discards everything that belongs to the
     * previous configuration and asks the user to reopen it. The error is
     * deferred while the app is hidden and shown by onActive().
     */
    (:typecheck(disableGlanceCheck))
    function onSettingsChanged() as Void {
        // Logger.debug( "OHApp.onSettingsChanged" );

        // The persisted data belongs to the previous configuration and must
        // not be restored: the next start may use different settings, and this
        // process may also be terminated right after this callback. This also
        // applies to the glance, which reads the sitemap label from the same
        // storage.
        try {
            Storage.clearValues();
            SitemapStore.clear();
        } catch( ex ) {
            Logger.debugException( ex );
        }

        // The glance process has no views and does not poll.
        if( AppLifecycle.isGlance() ) {
            return;
        }

        // Without a view, the app was instantiated for this callback only or is
        // still starting up. Nothing has been read or shown yet, so there is
        // nothing to stop and getInitialView() starts with the new settings.
        if( ViewStack.getCurrentView()[0] == null ) {
            // Logger.debug( "OHApp.onSettingsChanged: no view yet, nothing to stop" );
            return;
        }

        try {
            // Stop the app for good: polling is latched off, in-flight requests
            // are cancelled and the menu of the previous server is dropped.
            ExceptionHandler.stopForRestart();

            // Show the restart request, or defer it until the app is visible.
            ExceptionHandler.showOrDefer( new SettingsChangedException() );
        } catch( ex ) {
            // An exception must not escape an AppBase callback, as it would
            // terminate the app. There is no way back to the old
            // configuration, so this is reported as a fatal user interface error.
            ExceptionHandler.showOrDefer( new FatalUserInterfaceException( ex ) );
        }
    }

    // Switching into night mode may change the screen colors
    //
    // Theme-dependent resources are resolved lazily when the affected
    // views, menus and drawables are drawn. Views and menus that cache
    // such a resource refresh it before drawing, so requesting a redraw
    // is all that is needed to apply the new theme.
    (:typecheck(disableGlanceCheck))
    public function onNightModeChanged() as Void {
        // Logger.debug( "OHApp.onNightModeChanged" );
        
        // The ThemeManager exists only in the foreground scope.
        // In the glance scope, theme handling is very simple and
        // implemented directly in the onUpdate() of the glance view.
        if( ! AppLifecycle.isGlance() ) {
            NightModeTracker.get().update();
            ThemeManager.update();
        }
        
        WatchUi.requestUpdate();
    }
}