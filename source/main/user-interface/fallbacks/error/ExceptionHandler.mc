import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Math;

/*
 * The `ExceptionHandler` should be used throughout the widget to handle unexpected exceptions.
 *
 * Its behavior includes:
 * - Showing a toast notification for sitemap communication errors that do not
 *   require a full-screen error.
 * - Displaying a full-screen error view for those sitemap errors if the state is stale.
 * - Showing a toast notification for command communication errors, as these typically affect only 
 *   individual items and do not compromise the entire sitemap view.
 * - Displaying a full-screen error view immediately for all other errors.
 * - Stopping the app permanently for fatal user interface errors and settings changes, see
 *   `stopForRestart()`. This is the only case that requires a restart.
 *
 * Errors that cannot be displayed at the moment they occur (no view exists
 * yet, or the app is hidden by the system) are deferred and presented by
 * `consumeStartupException()` or `showPendingException()`.
 *
 * Full-screen errors are managed by `ErrorView`, while toast notifications are handled by `WarningToastHandler`.
 */
public class ExceptionHandler {
    /*
    * Note on DEFERRED EXCEPTIONS:
    *
    * An exception is deferred when it cannot be presented at the moment it
    * occurs: when no view exists yet (startup), or when the app is hidden by
    * the system (inactive lifecycle state), because no view must be switched
    * in while the app is not visible.
    *
    * Most exceptions during startup are handled directly by `OHApp`, which starts 
    * into an error view if needed. However, startup also triggers the first sitemap request. 
    * If this request fails immediately (e.g., with error -104: no phone), `onReceive()` 
    * may be called before any view is ready to display the error.
    *
    * The deferred value is a tuple consisting of the exception itself and a
    * flag indicating whether it must be shown as a full-screen error view
    * instead of a toast.
    *
    * A deferred exception is presented by
    * - consumeStartupException(), called by `OHApp.getInitialView()` when no
    *   view exists yet, or
    * - showPendingException(), called by `OHApp.onActive()` when the app
    *   becomes visible again.
    *
    * NOTE: The "force view" flag is not necessarily the same as
    * `exception.requiresFullScreenError()`. An exception might not require a
    * full-screen error in itself, but may still be treated as such if it has
    * persisted beyond a configured threshold.
    */
    typedef DeferredException as [Exception, Boolean];
    private static var _deferredException as DeferredException?;
    
    /*
    * Used for handling communication and general errors happening during background processing.
    * There may still be some places in the code where this is used for user interface exceptions, but
    * those instances should all be migrated to handleUserInterfaceException.
    */
    public static function handleBackgroundException( ex as Exception ) as Void {
        // Logger.debug( "ExceptionHandler.handleBackgroundException" );
        Logger.debugException( ex );

        var isSitemapFresh = SitemapStore.isSitemapFresh();
        
        // Supress emtpy responses
        // If 
        // - the setting to suppress empty response errors is enabled
        // - and this exception is classified as such
        // - and the state is still within the expiry time, 
        // we do nothing further
        // Reading the configuration may lead to an error, in which case we
        // process this exception with the remainder of this function
        try {
            if( ex instanceof CommunicationBaseException
                && ex.suppressAsEmptyResponse()
                && AppSettings.suppressEmptyResponseExceptions()
                && isSitemapFresh 
            ) {
                    // Logger.debug( "ExceptionHandler: Suppressing empty response" );
                    return;
            }
        } catch( ex1 ) {
            ex = ex1;
        }

        /*
        * A toast notification will be shown under the following conditions:
        * - Only if the current view has indicated to the `WarningToastHandler` that toasts are allowed.
        * - For all non-sitemap (i.e., command) communication errors.
        * - For sitemap errors that do not require a full-screen error and
        *   the state is still fresh (within the state expiry time).
        *
        * In all other cases, a full-screen error view is displayed instead.
        */
        if( WarningToastHandler.useToasts()
            && ex instanceof CommunicationBaseException 
            &&  ( ! ex.isFrom( CommunicationBaseException.EX_SOURCE_SITEMAP )
                  || ( isSitemapFresh && !ex.requiresFullScreenError() ) )
        ) {
            // Logger.debug( "ExceptionHandler: error shown as toast: " + ex.getToastMessage().toUpper() );
            
            // If there is no view yet, the exception is stored
            // as deferred exception, otherwise the toast will be shown.
            // While the app is hidden, WarningToastHandler suppresses it.
            if( ViewStack.getCurrentView()[0] == null ) {
                // Logger.debug( "ExceptionHandler: storing deferred toast exception" );
                _deferredException = [ex, false];
            } else {
                traceTemporary( ex );
                WarningToastHandler.showWarningWithId( toastId( ex ), ex.getToastMessage() );
            }
        } else {
            // Logger.debug( "ExceptionHandler: error requires full-screen view" );
            
            // A full-screen error clears the stored JSON to avoid showing
            // outdated data. Afterwards, the menu is only shown once a new
            // successful response is received. Without clearing the cache,
            // restarting the app would briefly show the menu before the error
            // happens again. Clearing the cache ensures the app goes directly
            // into a loading or error view until valid data is received.
            SitemapStore.invalidateSitemap();
            
            // Without a view, or while the app is hidden, the error must not
            // be displayed now. It is presented as soon as possible.
            showOrDefer( ex );
        }
    }


    /*
    * Used to handle errors from a sitemap sync that is automatically triggered on app startup.
    *
    * Errors that occur while sending a command or during a user-triggered sitemap sync
    * are already handled by the sync API and are shown in the sync confirmation view.
    * No additional error handling by the app is required in those cases.
    */
    public static function handleSyncException( ex as Exception ) as Void {
        // Logger.debug( "ExceptionHandler.handleSyncException" );
        Logger.debugException( ex );

        // Without a view, or while the app is hidden, the error is deferred.
        showOrDefer( ex );
    }


    /*
    * Used for handling errors coming from user interface interactions.
    */
    public static function handleUserInterfaceException( ex as Exception ) as Void {
        // Logger.debug( "ExceptionHandler.handleUserInterfaceException" );
        Logger.debugException( ex );

        // While this should not happen, we check if we are in the startup phase,
        // and if yes, ignore the exception
        if( ViewStack.getCurrentView()[0] != null ) {
            // NonFatalUserInterfaceException is used for non-fatal UI exceptions, for which
            // we will show a toast notification
            if( ex instanceof NonFatalUserInterfaceException ) {
                traceTemporary( ex );
                WarningToastHandler.showWarningWithId( toastId( ex ), ex.getToastMessage() );
            } else {
                // A fatal UI error ends the app: polling is latched off, in-flight
                // requests are cancelled and the menu is dropped, so nothing from
                // the previous state can reappear behind the error view. Only an
                // app restart resumes the app.
                stopForRestart();
                
                // If the exception is not already a FatalUserInterfaceException, we wrap
                // it in one
                if( ! ( ex instanceof FatalUserInterfaceException ) ) {
                    ex = new FatalUserInterfaceException( ex );
                }
                
                // Show the error view
                showOrDefer( ex );
            }
        }
    }


    /*
    * Stops the app for good. Used when the app cannot continue with its current
    * in-memory state, for example after a fatal user interface error or a
    * settings change. Only an app restart resumes the app.
    *
    * - Polling is latched off, so no further request is issued or scheduled.
    * - In-flight requests are cancelled, including commands and a Wi-Fi sync.
    *   Their REQUEST_CANCELLED responses are ignored by the request classes.
    * - The menu is dropped, so no view of the previous state can be shown again.
    *
    * The order matters: requests are cancelled before the menu is cleared,
    * because the cancellation callbacks run synchronously and still access the
    * menu items.
    *
    * Each step is attempted independently, so that a failure does not prevent
    * the remaining steps and no exception escapes into a lifecycle callback or
    * an input handler.
    */
    public static function stopForRestart() as Void {
        // Logger.debug( "ExceptionHandler.stopForRestart" );

        try {
            SitemapRequest.get().stopPermanently();
        } catch( ex ) {
            Logger.debugException( ex );
        }

        try {
            BaseRequest.cancelAllRequests();
        } catch( ex ) {
            Logger.debugException( ex );
        }

        try {
            HomepageMenu.clear();
        } catch( ex ) {
            Logger.debugException( ex );
        }
    }


    /*
    * Presents a full-screen error, or defers it when no view exists yet
    * (startup) or while the app is hidden by the system. Deferred errors are
    * presented by consumeStartupException() or showPendingException().
    */
    public static function showOrDefer( ex as Exception ) as Void {
        traceShownError( ex );

        if( ViewStack.getCurrentView()[0] == null || ! AppLifecycle.isActive() ) {
            // Logger.debug( "ExceptionHandler: deferring exception" );
            _deferredException = [ex, true];
        } else {
            ErrorView.showOrUpdate( ex );
        }
    }


    /*
    * This function must be called by views by `OHApp` in getInitialView().
    *
    * Depending on the type of exception that occurred during startup, 
    * it will display either a toast notification or return a full-screen 
    * error view.
    */
    public static function consumeStartupException( useToast as Boolean ) as ErrorView? {
        if( _deferredException != null ) {
            var deferredException = _deferredException as DeferredException;
            _deferredException = null;
            var ex = deferredException[0];
            
            /*
            * A toast is shown if the deferred exception is a non-fatal 
            * communication error and the calling view has enabled toast notifications.
            */
            if( useToast && !deferredException[1] && ex instanceof CommunicationBaseException ) {
                traceTemporary( ex );
                WarningToastHandler.showWarningWithId( toastId( ex ), ex.getToastMessage() );
            } else {
                // A deferred toast-classified error is escalated to a
                // full-screen error here; trace the classification it now gets.
                if( ! deferredException[1] ) {
                    traceShownError( ex );
                }
                return ErrorView.createOrUpdate( ex );
            }
        }
        return null;
    }

    /*
    * Presents an exception that was deferred while the app was hidden.
    * Must be called when the app becomes visible again, see OHApp.onActive().
    *
    * A deferred startup exception stays deferred: it is presented by
    * consumeStartupException() together with the initial view, because there
    * is no view to switch to yet.
    */
    public static function showPendingException() as Void {
        if( _deferredException == null ) {
            return;
        }

        if( ! AppLifecycle.isActive() ) {
            return;
        }

        if( ViewStack.getCurrentView()[0] == null ) {
            // Still in startup, consumeStartupException() takes over
            return;
        }

        var deferredException = _deferredException as DeferredException;
        _deferredException = null;
        var ex = deferredException[0];

        if( ! deferredException[1]
            && WarningToastHandler.useToasts()
            && ex instanceof CommunicationBaseException
        ) {
            traceTemporary( ex );
            WarningToastHandler.showWarningWithId( toastId( ex ), ex.getToastMessage() );
        } else {
            // A deferred toast-classified error is escalated to a full-screen
            // error here; trace the classification it now gets.
            if( ! deferredException[1] ) {
                traceShownError( ex );
            }
            ErrorView.showOrUpdate( ex );
        }
    }

    /*
     * Debug-only E2E traces for the error handling classification. The release
     * implementations are empty, so no classification or string building is
     * present in release builds.
     */
    (:debug)
    private static function traceTemporary( ex as Exception ) as Void {
        E2ETrace.errorTemporary(
            E2ETraceExceptions.identifier( ex ),
            E2ETraceExceptions.code( ex ),
            E2ETraceExceptions.source( ex )
        );
    }
    (:release)
    private static function traceTemporary( ex as Exception ) as Void {}

    // A full-screen error is fatal if it requires an app restart, otherwise
    // critical: the app keeps running and keeps polling.
    (:debug)
    private static function traceShownError( ex as Exception ) as Void {
        var id = E2ETraceExceptions.identifier( ex );
        var code = E2ETraceExceptions.code( ex );
        var source = E2ETraceExceptions.source( ex );
        if( ex instanceof FatalUserInterfaceException || ex instanceof SettingsChangedException ) {
            E2ETrace.errorFatal( id, code, source );
        } else {
            E2ETrace.errorCritical( id, code, source );
        }
    }
    (:release)
    private static function traceShownError( ex as Exception ) as Void {}

    // Stable, language-independent id for the toast event. Empty in release
    // builds, where it is not used.
    (:debug)
    private static function toastId( ex as Exception ) as String {
        return E2ETraceExceptions.errorId( ex );
    }
    (:release)
    private static function toastId( ex as Exception ) as String {
        return "";
    }
}
