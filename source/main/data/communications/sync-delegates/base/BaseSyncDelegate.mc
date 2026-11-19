import Toybox.Lang;
import Toybox.Communications;
import Toybox.WatchUi;

/* Base class for sync delegates.
 *
 * In the CIQ API, sync delegates perform tasks that require a Wi-Fi
 * connection. Due to battery constraints on Garmin devices, Wi-Fi is
 * only available on demand and for a limited time. An app can request
 * a Wi-Fi–based sync via Communications.startSync(), which is wrapped
 * by this class.
 *
 * Lifecycle overview:
 *
 * - startSync()
 *   Called by the app to request entering sync mode.
 *   Stores the delegate that initiated the sync in the static
 *   member _currentSyncDelegate.
 *
 * - getCurrentSyncDelegate()
 *   The CIQ API calls OHApp.getSyncDelegate() to retrieve the
 *   active sync delegate. OHApp.getSyncDelegate() in turn
 *   uses this function, which returns the value stored in
 *   _currentSyncDelegate.
 *
 * - onStartSync()
 *   Invoked by the CIQ API once a Wi-Fi connection has been established.
 *   This method performs common setup and then calls performSync().
 *
 * - performSync()
 *   Abstract method to be implemented by subclasses. Contains the
 *   actual sync logic.
 *
 * - finishSync()
 *   Must be called by subclasses when the sync completes successfully.
 *   Exits sync mode.
 *
 * - onSyncException()
 *   Should be called by subclasses if an error occurs during syncing.
 *   Exits sync mode and displays an error message to the user.
 *
 * - onStopSync()
 *   Invoked by the CIQ API.
 *   The behavior of the API regarding onStopSync differs across devices and 
 *   the simulator. According to the documentation, it should only be invoked 
 *   when the user cancels an active sync, which is also how the simulator behaves.
 *
 *   However, on an Epix Pro Gen 2, onStopSync is additionally called when
 *   Communication.notifySyncComplete() is invoked with a non-null parameter
 *   (i.e. an error message). On an Edge 850, it is even called after a
 *   successful sync, once the user has confirmed the success screen.
 *
 *   To handle these inconsistencies, we invoke onSyncFinished() from
 *   onSyncException(), finishSync(), and onStopSync(). The implementation of
 *   onSyncFinished() is designed to tolerate multiple invocations and
 *   guarantees that its actions are executed exactly once.
 * 
 * - onSyncFinished()
 *   Internal helper that performs the finalization steps of the sync.
 *   It may be invoked multiple times and uses the _hasSyncFinished flag
 *   to ensure that all associated actions, especially task scheduling,
 *   are executed exactly once. 
 *
 * See the following link for API documentation:
 * https://forums.garmin.com/developer/connect-iq/b/news-announcements/posts/connect-iq-3-1-connects-you-to-the-world
 *
 * IMPORTANT: When calling functions of this class from code that may also run
 * on devices without Wi-Fi support, always use SafeSitemapSyncDelegate as a wrapper.
 * See that class for details.
 *
 * At the moment, this requirement only applies to the SitemapSyncDelegate
 * implementation, not to CommandSyncDelegate.
 */
class BaseSyncDelegate extends SyncDelegate {

    /******* STATIC *******/ 

    // True if the sync delegate requests a confirmation of
    // Wi-Fi availablity. See consumeWifiCheckRequest() for details.
    private static var _wifiCheckRequest as Boolean = false;

    // Stores the instance of the sync delegate which was used last
    // See getCurrentSyncDelegate() for details.
    private static var _currentSyncDelegate as BaseSyncDelegate?;

    // Returns true if any sync delegate requests confirmation of Wi-Fi availability.
    //
    // If Bluetooth is not connected, Wi-Fi availability is checked only once.
    // After switching to Wi-Fi mode, no periodic checks are performed because
    // those checks would block the app from sending Wi-Fi commands.
    //
    // If a Wi-Fi sync fails, Wi-Fi should be verified again after the sync ends
    // to ensure it is still available.
    //
    // Unfortunately, there is no specific error indicating that a sync failed
    // due to missing Wi-Fi. Instead, only onStopSync() is called. Since this
    // callback is also triggered for other error conditions and when the user
    // cancels the sync, we cannot reliably distinguish the cause.
    //
    // Therefore, as a precaution, we always request a Wi-Fi check whenever
    // onStopSync() is invoked.
    //
    // Note: On the physical Edge 850 (and possibly other devices),
    // onStopSync() is invoked even after a successful sync.
    // As a result, the Wi-Fi check is performed after every sync,
    // regardless of whether it succeeded, failed, or was cancelled.
    public static function consumeWifiCheckRequest() as Boolean {
        var wifiCheckRequest = _wifiCheckRequest;
        _wifiCheckRequest = false;
        return wifiCheckRequest;
    }

    // Returns the currently active sync delegate.
    // Used by OHApp.getSyncDelegate().
    //
    // _currentSyncDelegate is set by startSync() and remains valid until the
    // next sync is triggered.
    //
    // There is no reliable point at which we can assume that the Connect IQ
    // runtime no longer needs the sync delegate, so it cannot be cleared
    // immediately after sync completion.
    //
    // Background: The CIQ API calls OHApp.getSyncDelegate() multiple times
    // during a sync. Even after Communication.notifySyncComplete() has been
    // invoked, the runtime may still call OHApp.getSyncDelegate() in order to
    // trigger onStopSync(). This behavior occurs only on some devices
    // (see lifecycle notes at the beginning of this file).
    //
    // Because of this, there is no safe moment to clear _currentSyncDelegate
    // after a sync has finished. Therefore, it remains in place until the
    // next sync starts.
    public static function getCurrentSyncDelegate() as BaseSyncDelegate? {
        return _currentSyncDelegate;
    }


    /******* INSTANCE *******/ 

    // Set to true once a sync has concluded
    // See onSyncFinished() for details
    private var _hasSyncFinished as Boolean = false;

    // Set to true while the sync is in progress
    private var _isSyncInProgress as Boolean = false;

    // See isSyncNeeded()
    private var _isSyncNeeded as Boolean = false;

    // Holds the result of the last sync
    private var _lastSyncState as WifiSyncResult = RESET_SYNC_STATE;

    // Constructor
    public function initialize() {
        SyncDelegate.initialize();
    }

    // Returns the result of the last sync and resets the stored result
    // ATTENTION: See the note on SafeSitemapSyncDelegate in this class description
    // before using this function.
    public function consumeLastSyncResult() as WifiSyncResult {
        var lastSyncState = _lastSyncState;
        _lastSyncState = RESET_SYNC_STATE;
        return lastSyncState;
    }

    // Exits sync mode. Subclasses must call this method when their sync
    // operation has completed successfully.
    public function finishSync() as Void {
        // Logger.debug( "BaseSyncDelegate.finishSync" );
        onSyncFinished();
        Communications.notifySyncComplete( null );
        // Logger.debug( "BaseSyncDelegate.finishSync end" );
    }

    // Can be used by other classes to adjust their behavior depending on
    // whether the application is currently in sync mode. For example,
    // exception handling differs, and sitemap responses must not trigger
    // any screen updates while sync mode is active.
    // ATTENTION: See the note on SafeSitemapSyncDelegate in this class description
    // before using this function.
    public function isSyncInProgress() as Boolean {
        // Logger.debug( "BaseSyncDelegate.isSyncInProgress=" + _isSyncInProgress );
        return _isSyncInProgress;
    }

    // Called by the API to determine whether a sync is required.
    // In the simulator, after a sync has completed, the CIQ API
    // may retrieve the sync delegate again and call isSyncNeeded().
    // If it returns true, another sync is started immediately.
    //
    // To prevent this, we set _isSyncNeeded to true when a sync
    // starts and reset it to false once the sync has finished.
    public function isSyncNeeded() as Boolean {
        // Logger.debug( "BaseSyncDelegate.isSyncNeeded=" + _isSyncNeeded );
        return _isSyncNeeded;
    }

    // Subclasses must call this method when their sync operation fails.
    // It exits sync mode and displays an error message to the user.
    public function onSyncException( ex as Exception ) as Void {
        // Logger.debug( "BaseSyncDelegate.onSyncException" );
        
        onSyncFinished();
        
        _lastSyncState[1] = ex;

        // There is not much space for the error message presented by
        // the Garmin sync screens, therefore if we can, we shorten it
        Communications.notifySyncComplete( 
            ex instanceof CommunicationException
            ? "Comm Error " + ex.getToastMessage()
            : ex.getErrorMessage() 
        );

        // Logger.debug( "BaseSyncDelegate.onSyncException end" );
    }

    // Called by the API to start the sync.
    // Reports initial progress and then calls performSync(), where
    // subclasses implement the actual sync logic. Exception handling
    // is handled here, so performSync implementation may throw 
    // exceptions and expect them to be handled.
    public function onStartSync() as Void {
        // Logger.debug( "BaseSyncDelegate.onStartSync" );
        try {
            // Logger.debugConnectionInfo();

            _isSyncInProgress = true;

            // Start with 20% progress
            Communications.notifySyncProgress( 20 );

            performSync();
            
        } catch( ex ) {
            onSyncException( ex );
        }
        // Logger.debug( "BaseSyncDelegate.onStartSync end" );
    }

    // Called by the API when the user interrupts the sync
    // or when the sync is ended by calling Communications.notifySyncComplete
    // with an error message.
    // Cancels all pending requests and exits sync mode.
    public function onStopSync() as Void {
        // Logger.debug( "BaseSyncDelegate.onStopSync" );
        
        // See consumeWifiCheckRequest() for details
        _wifiCheckRequest = true;
        
        BaseRequest.cancelAllRequests();
        onSyncFinished();
        
        // Logger.debug( "BaseSyncDelegate.onStopSync end" );
    }

    // Function used by all code paths that stop the sync.
    // Resets internal state and schedules the task that restarts the
    // sitemap request.
    // 
    // This function may be invoked multiple times (see the lifecycle and
    // onStopSync notes at the top of this file). The _hasSyncFinished flag
    // ensures that all associated actions, especially task scheduling,
    // are executed exactly once.
    //
    // Note: The sitemap request must not be started directly from the
    // sync delegate. At that point it may still run on the Wi-Fi
    // connection, which is likely to be closed before the request
    // completes.
    public function onSyncFinished() as Void {
        // Logger.debug( "BaseSyncDelegate.onSyncFinished" );
        if( ! _hasSyncFinished ) {
            // Logger.debug( "BaseSyncDelegate.onSyncFinished: cleaning up!" );
 
            // See isSyncNeeded() for details.
            _isSyncNeeded = false;
            
            // The sync is no longer in progress, and the last sync state
            // is updated to indicate that a sync has completed.
            _isSyncInProgress = false;
            _lastSyncState[0] = true;
 
            // Unblock the sitemap request loop. The request itself is issued
            // by PostSyncTask, because it must not run on the Wi-Fi connection
            // of the sync. Unblocking here keeps the state consistent if that
            // task is lost: the next resume()/endCommand() resumes polling.
            SitemapRequest.get().endSync( false );

            AsyncTaskQueue.get().add( new PostSyncTask() );
 
            // See the class-level comment on onStopSync() for details.
            _hasSyncFinished = true;
        } else {
            // Logger.debug( "BaseSyncDelegate.onSyncFinished: was called already, doing nothing!" );
        }
    }

    // To be implemented by subclasses to perform the actual sync logic.
    // Implementations may throw exceptions, which are handled by onStartSync(),
    // the method that invokes this function.
    public function performSync() as Void {
        throw new AbstractMethodException( "BaseSyncDelegate.performSync" );
    }

    // Starts a sync request and displays the provided message to the user.
    // Marks this instance as needing a sync, stops the sitemap request timer,
    // and requests sync mode from the CIQ API.
    public function startSync( msg as String ) as Void {
        // Logger.debug( "BaseSyncDelegate.startSync" );
        var syncStarted = false;
        try {
            // Logger.debugConnectionInfo();
            
            _currentSyncDelegate = self;
            syncStarted = SitemapRequest.get().beginSync();

            // Newer API versions support displaying a custom message in the sync view
            if( Communications has :startSync2 ) {
                Communications.startSync2( { :message => msg } );
            } else {
                Communications.startSync();
            }
            
            // Flags are set at the end so that if the code above throws an
            // exception, they remain at their previous values.
            _isSyncNeeded = true;
            _hasSyncFinished = false;
        } catch( ex ) {
            // Logger.debug( "BaseSyncDelegate.startSync: exception, restarting sitemap request" );
            // Do not unblock a sync that was already in progress
            if( syncStarted ) {
                SitemapRequest.get().endSync( true );
            }
            throw ex;
        }
    }
}