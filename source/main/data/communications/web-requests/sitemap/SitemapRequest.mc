import Toybox.Lang;
import Toybox.Communications;
import Toybox.PersistedContent;
import Toybox.Application.Storage;
import Toybox.Application;
import Toybox.Timer;

/*
 * This class handles web requests for sitemap data and follows a mode-keyed Singleton pattern.
 * One instance is used for normal operation, and a second instance is used during Wi-Fi sync.
 * During Wi-Fi sync, no phone connection is available, so the normal instance cannot be used.
 * The user can manually trigger a sitemap update via Wi-Fi from the settings menu.
 * 
 * Responsibilities:
 * - Constructs the URL for the sitemap request.
 *
 * - Implements Communication.makeWebRequest:
 *     - start(): initiates the first web request.
 *     - After each response, schedules the next request based on the polling interval.
 *       If the polling interval is 0, the next request is triggered immediately.
 *     - Polling is suspended while the app is hidden, while a command request is
 *       in flight and while a Wi-Fi sync runs, and stopped permanently on shutdown
 *       or after a fatal error. See start(), pause()/resume(), beginCommand()/
 *       endCommand(), beginSync()/endSync(), fail() and shutdown().
 *
 * - Implements onReceive() to process responses:
 *     - On success, forwards the incoming JSON to the `SitemapProcessor`.
 *
 * - Implements handleError() to process errors that occur in onReceive() or within
 *   the `SitemapProcessor`. This function schedules the next request based on both
 *   the minimum error polling interval and the configured regular polling interval.
 *
 * - Implements logic for triggering the next request, applying the appropriate delay
 *   based on the current polling configuration.
 */
class SitemapRequest extends BaseRequest {
    
    /******* STATIC *******/ 

    // Singleton instance and accessor
    private static var _instance as SitemapRequest?;
    private static var _syncInstance as SitemapRequest?;

    // Returns the appropriate Singleton instance based on the current mode.
    public static function get() as SitemapRequest {
        if( SafeSitemapSyncDelegate.isSyncInProgress() ) {
            if( _syncInstance == null ) {
                _syncInstance = new SitemapRequest();
            }
            // Logger.debug( "SitemapRequest.get: returning sync instance" );
            return _syncInstance as SitemapRequest;
        } else {
            if( _instance == null ) {
                _instance = new SitemapRequest();
            }
            // Logger.debug( "SitemapRequest.get: returning main instance" );
            return _instance as SitemapRequest;
        }
    }

    // Removes the sync singleton to conserve memory
    public static function resetSyncInstance() as Void {
        _syncInstance = null;
    }

    /******* INSTANCE *******/ 

    public const SITEMAP_ERROR_MINIMUM_POLLING_INTERVAL as Number = 1000;

    // Defines the source value to be used for exception handling
    private const SOURCE as CommunicationBaseException.Source = CommunicationBaseException.EX_SOURCE_SITEMAP;

    // The assembled URL for the request
    private var _url as String;
    
    // Reasons why the polling loop must not issue or schedule requests.
    // Polling is allowed only when none of them applies. The state is static
    // because SitemapRequest has a separate instance for sync mode, but there
    // is only one polling loop.
    private static var _isStarted as Boolean = false;    // getInitialView() has run
    private static var _isHidden as Boolean = false;     // app is inactive
    private static var _isSyncing as Boolean = false;    // Wi-Fi sync in progress
    private static var _isFatal as Boolean = false;      // latched after a fatal error
    private static var _isShutdown as Boolean = false;   // latched on termination
    private static var _commandsInFlight as Number = 0;  // command requests, may overlap

    // Cached result of canPoll(), used to detect state transitions.
    private static var _isPollingAllowed as Boolean = false;

    // _hasPendingRequest is true if we are inbetween a makeRequest()
    // and an onReceive
    private var _hasPendingRequest as Boolean = false;
    // If polling becomes blocked and there is a pending request, this
    // is set to true to instruct onReceive() to ignore the
    // next incoming response.
    private var _ignoreNextResponse as Boolean = false;

    // Members for controlling the web request loop
    private var _pollingInterval as Number;
    private var _timer as Timer.Timer = new Timer.Timer();

    // Stores the number of requests sent, for debugging
    // purposes
    private var _requestCount as Number = 0;
    private var _responseCount as Number = 0;

    // Store the memory usage before making the request, to
    // estimate the memory used by the received JSON
    // See `SitemapStore` for details.
    private var _memoryUsedBeforeRequest as Number = 0;

    // Constructor
    private function initialize() {
        // Initialize base class
        BaseRequest.initialize( Communications.HTTP_REQUEST_METHOD_GET );
        // Set response content type
        setOption( :responseType, Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON );

        // Set the polling interval
        _pollingInterval = AppSettings.getPollingInterval();
        
        // Assemble the URL
        _url = AppSettings.getUrl() + "rest/sitemaps/" + AppSettings.getSitemap();
    }

    // Handles exceptions from onReceive() and the SitemapProcessor.
    // The exception is passed to the ExceptionHandler, and the next
    // request is scheduled using a delay based on the greater of:
    // - the error polling interval, or
    // - the configured regular polling interval.
    public function handleException( ex as Exception ) as Void {
        // Logger.debug( "SitemapRequest.handleException" );
        // Logger.debugMemory( null );

        // If an out-of-memory error occurs, it might be due to a sitemap change
        // where parts of the old and new menu structures coexist in memory.
        // To prevent this, we clear the menu so the next request starts fresh.
        if( ex instanceof OutOfMemoryException ) {
            HomepageMenu.clear();
        }

        // During sync, all errors are handled by the sync delegate
        if( SafeSitemapSyncDelegate.isSyncInProgress() ) {
            SitemapSyncDelegate.get().onSyncException( ex );
        } else {

            // If Communications.makeWebRequest() returns an error indicating
            // that no BLE connection to the phone is available, we fall back
            // to Wi-Fi.
            //
            // Although makeWebRequestPeriodic() checks phone connectivity via
            // DeviceSettings.connectionInfo beforehand, makeWebRequest() may
            // still return a BLE error, for example if the connection was lost
            // between the check and the actual request.
            if( ex instanceof CommunicationException && ex.isNoPhone() ) {
                ConnectionManager.get().tryWifiConnectionAndTriggerNextRequest();
            } else {
                // Logger.debug( "ExceptionHandler: confirming successful connection." );
                
                ConnectionManager.get().confirmPhoneConnection();

                ExceptionHandler.handleBackgroundException( ex );
                
                // If an error occurs during processing the sitemap from
                // storage, the request is already schedule and we do 
                // not need to to it anymore
                if( ! _hasPendingRequest ) {
                    triggerNextRequest( true );
                }
            }
        }
    }

    // Makes the web request
    private function makeRequestInternal( singleRequest as Boolean ) as Void {
        // We make the request only if
        // - polling is allowed (started, visible, no command or sync in
        //   progress and not stopped permanently)
        // - or it is a single request, which does not use the timer
        // - and there is no pending request
        if( ( canPoll() || singleRequest ) && ! _hasPendingRequest ) {
            _requestCount++;
            // Logger.debug( "SitemapRequest.makeRequestInternal (#" + _requestCount + ")" );
            // Logger.debugMemory( null );
            
            // _hasPendingRequest has to be set to true BEFORE makeWebRequest
            // For some errors (like -104/no phone), on receive is called
            // synchronously by makeWebRequest. If in this case, 
            // _hasPendingRequest would come after makeWebRequest it would
            // be set to true without there being any pending web request,
            // which would cancel the next request and thus stop the
            // sitemap request loop
            _hasPendingRequest = true;
            Communications.makeWebRequest( _url, null, getBaseOptions(), method( :onReceive ) );
            _memoryUsedBeforeRequest = System.getSystemStats().usedMemory;
        } else {
            // Logger.debug( "SitemapRequest.makeRequest: stopped or has pending request, not executed" );
        }
    }

    // Executes web requests as part of the timer-based loop.
    // Timer-driven requests are performed via the phone connection only.
    // This function checks whether the phone connection is enabled
    // in the system settings. If it is not available, a Wi-Fi
    // connectivity check is initiated instead.
    public function makeRequestPeriodic() as Void {
        // Logger.debug( "SitemapRequest.onTimerMakeRequest" );
        if( ConnectionManager.get().isPhoneConnectedAccordingToSettings() ) {
            // Logger.debug( "SitemapRequest.onTimerMakeRequest: is on phone according to settings" );
            makeRequestInternal( false );
        } else {
            // Logger.debug( "SitemapRequest.onTimerMakeRequest: not on phone, trying Wi-Fi" );
            ConnectionManager.get().tryWifiConnectionAndTriggerNextRequest();
        }
    }

    // Executes a single web request.
    // Can be called independently of the sitemap request loop.
    // Used by the command sync delegate to initiate a sitemap
    // update via Wi-Fi.
    public function makeRequestSingle() as Void {
        makeRequestInternal( true );
    }

    // Processes the response
    // The data type must match the callback signature of Communications.makeWebRequest exactly.
    public function onReceive( 
        responseCode as Number, 
        data as Dictionary 
            or String 
            or ByteArray 
            or PersistedContent.Iterator 
            or Null 
    ) as Void {
        _hasPendingRequest = false;
        _responseCount++;
        // Logger.debug( "SitemapRequest.onReceive: start (#" + _responseCount + ")" );

        // When polling becomes blocked while a request is pending,
        // _ignoreNextResponse is set true. onReceive() acts on this,
        // ignores the next response and resets the member
        if( _ignoreNextResponse ) {
            // Logger.debug( "SitemapRequest.onReceive: ignoring this response");
            _ignoreNextResponse = false;
            triggerNextRequest( false );
        } else {
            try {
                // Verify response code and response data (in the call to process)
                // These functions of the base class throw an exception if the 
                // code/data is not OK. Additionally checkResponseCode may return
                // false in conditions where no error is raised but the response
                // shall be ignored
                if( checkResponseCode( responseCode, SOURCE ) ) {
                    
                    // Any positive result outside of sync mode indicates that the
                    // phone connection is working.
                    // The exception handler also performs this confirmation
                    // for all errors except no-phone errors.
                    if( ! SafeSitemapSyncDelegate.isSyncInProgress() ) {
                        // Logger.debug( "SitemapRequest.onReceive: confirming successful connection." );
                        ConnectionManager.get().confirmPhoneConnection();
                    }
                    
                    // The JSON is processed by processIncomingJson()
                    processIncomingJson( 
                        new SitemapJsonIncoming( 
                            checkResponse( data, SOURCE ), 
                            System.getSystemStats().usedMemory
                                - _memoryUsedBeforeRequest 
                        )
                    );
                }
            } catch( ex ) {
                // Calling the handler for exceptions
                // Note: SitemapRequest.handleException differentiates between
                // normal mode and sync mode, and in the case of the latter
                // passes the exception on to the sync delegate
                // Logger.debug( "SitemapRequest.onReceive: exception");
                handleException( ex );
            }
        }
        // Logger.debug( "SitemapRequest.onReceive: end");
    }

    // Called by the request loop timer to initiate a request.
    // Wraps makeRequestPeriodic() with exception handling.
    public function onTimerMakeRequest() as Void {
        try {
            makeRequestPeriodic();
        } catch ( ex ) {
            SitemapRequest.handleException( ex );
        }
    }

    /*
    * Processes the incoming JSON data by:
    * - Updating the SitemapStore
    * - Asynchronously creating or updating the menu structure
    * - Passing any exceptions to the handleException() function below
    * - Triggering the next request
    *
    * The work is broken down into small tasks and processed via the AsyncTaskQueue.
    * This allows recursive data structures to be handled iteratively, avoiding stack overflows
    * and reducing the risk of prolonged code execution errors
    * (e.g., "Watchdog Tripped Error - Code Executed Too Long").
    * It also helps maintain UI responsiveness.
    *
    * For task sequencing details, see SitemapRequestTasks.mc.
    */
    private function processIncomingJson( incomingJson as SitemapJsonIncoming ) as Void {
        // Logger.debug( "SitemapRequest.incomingJson" );

        var taskQueue = AsyncTaskQueue.get();

        // Under normal circumstances, the queue should be empty at this point,
        // as each new request is only triggered as the final step of processing
        // the previous one. The only exception is during startup, when an initial
        // request is sent immediately and processed in parallel with the asynchronous
        // loading of the sitemap from storage. In this case, it's possible that the
        // response arrives before the sitemap has finished loading. If that happens,
        // we cancel any remaining tasks and proceed directly with processing the response.
        if( ! taskQueue.isEmpty() ) {
            // Logger.debug( "SitemapRequest encountered non-empty task queue" );
            taskQueue.removeAll();
        }

        // If the menu hasn’t been created yet, we're likely in a non-interactive
        // loading or error view. In this state, we prioritize speed over responsiveness
        // to complete processing as quickly as possible. The same applies to
        // sync mode.
        if( ! HomepageMenu.exists() || SafeSitemapSyncDelegate.isSyncInProgress() ) {
            taskQueue.prioritizeSpeed();
        } else {
            taskQueue.prioritizeResponsiveness();
        }

        // Start with the first task; it will handle scheduling all 
        // subsequent tasks as needed.
        taskQueue.add( new ProcessIncomingJsonTask( incomingJson ) );
    }

    // True if the polling loop may issue and schedule requests.
    // Requests made with makeRequestSingle() are not affected.
    private static function canPoll() as Boolean {
        return _isStarted
               && ! _isHidden
               && ! _isSyncing
               && ! _isFatal
               && ! _isShutdown
               && _commandsInFlight == 0;
    }

    // Re-evaluates the polling state after one of the block flags changed.
    // On the transition to blocked the timer is stopped and a pending response
    // is ignored, so nothing runs while polling is not allowed. On the
    // transition to allowed a request is issued if requestNow is true.
    private function updatePollingState( requestNow as Boolean ) as Void {
        var allowed = canPoll();
        if( allowed == _isPollingAllowed ) {
            return;
        }
        _isPollingAllowed = allowed;

        if( allowed ) {
            if( requestNow && ! _hasPendingRequest ) {
                // Logger.debug( "SitemapRequest: polling resumed" );
                makeRequestPeriodic();
            }
        } else {
            // Logger.debug( "SitemapRequest: polling suspended" );
            _timer.stop();
            if( _hasPendingRequest ) {
                _ignoreNextResponse = true;
            }
            // Asynchronous processing is discarded, unless a Wi-Fi sync is in
            // progress: the sync relies on its tasks, which use the same queue.
            if( ! _isSyncing ) {
                AsyncTaskQueue.get().removeAll();
            }
        }
    }

    // Starts the request loop. Called from getInitialView().
    // Calling this more than once is a no-op.
    public function start() as Void {
        // Logger.debug( "SitemapRequest.start" );
        _isStarted = true;
        updatePollingState( true );
    }

    // Stops the request loop permanently. Called from OHApp.onStop().
    public function shutdown() as Void {
        // Logger.debug( "SitemapRequest.shutdown" );
        _isShutdown = true;
        updatePollingState( false );
        // Nothing may run during teardown, even if a sync was in progress
        AsyncTaskQueue.get().removeAll();
    }

    // Stops the request loop permanently after a fatal error.
    // Only an app restart resumes polling.
    public function fail() as Void {
        // Logger.debug( "SitemapRequest.fail" );
        _isFatal = true;
        updatePollingState( false );
    }

    // Suspends the request loop while the app is hidden by the system.
    // Called from OHApp.onInactive().
    public function pause() as Void {
        // Logger.debug( "SitemapRequest.pause" );
        _isHidden = true;
        updatePollingState( false );
    }

    // Resumes the request loop when the app becomes visible again.
    // Called from OHApp.onActive().
    public function resume() as Void {
        // Logger.debug( "SitemapRequest.resume" );
        _isHidden = false;
        updatePollingState( true );
    }

    // Suspends the request loop while a command request is in flight.
    // Called from BaseCommandRequest. Several commands may overlap.
    public function beginCommand() as Void {
        // Logger.debug( "SitemapRequest.beginCommand" );
        _commandsInFlight++;
        updatePollingState( false );
    }

    // Called when a command request has completed (response, error or cancel).
    public function endCommand() as Void {
        // Logger.debug( "SitemapRequest.endCommand" );
        if( _commandsInFlight > 0 ) {
            _commandsInFlight--;
        }
        updatePollingState( true );
    }

    // Suspends the request loop while a Wi-Fi sync is in progress.
    // Called from BaseSyncDelegate.startSync().
    // Returns true if this call started the sync block, false if a sync was
    // already in progress. Only the caller that started it may end it.
    public function beginSync() as Boolean {
        // Logger.debug( "SitemapRequest.beginSync" );
        if( _isSyncing ) {
            return false;
        }
        _isSyncing = true;
        // Queued sitemap processing belongs to the regular loop and must not
        // run against the sync instance.
        AsyncTaskQueue.get().removeAll();
        updatePollingState( false );
        return true;
    }

    // Called when the Wi-Fi sync has ended.
    // With requestImmediately = false the loop is unblocked but no request is
    // issued. PostSyncTask then calls requestNow() once the Wi-Fi connection of
    // the sync is gone. If that task is lost, the next resume(), endCommand()
    // or start() re-evaluates the state and resumes polling.
    public function endSync( requestImmediately as Boolean ) as Void {
        // Logger.debug( "SitemapRequest.endSync" );
        _isSyncing = false;
        updatePollingState( requestImmediately );
    }

    // Issues a request now if polling is allowed. Used by PostSyncTask.
    public function requestNow() as Void {
        // Logger.debug( "SitemapRequest.requestNow" );
        if( canPoll() && ! _hasPendingRequest ) {
            makeRequestPeriodic();
        }
    }

    // Used to trigger the next request after the current response has been
    // successfully processed.
    public function triggerNextRequest( applyErrorMinimumInterval as Boolean ) as Void {
        // Logger.debug( "SitemapRequest.triggerNextRequestInternal" );

        // Do not schedule requests while polling is not allowed, e.g. when a
        // response arrives after shutdown, while the app is hidden or while a
        // command or sync is in progress
        if( ! canPoll() ) {
            return;
        }

        // In case of errors, we apply a set minimum interval of 1 seconds
        var delay = _pollingInterval > SITEMAP_ERROR_MINIMUM_POLLING_INTERVAL
                    || ! applyErrorMinimumInterval
                        ? _pollingInterval
                        : SITEMAP_ERROR_MINIMUM_POLLING_INTERVAL;
        
        // Depending on the delay the next request is
        // scheduled via timer or triggered immediately
        if( delay > 0 ) {
            // Logger.debug( "SitemapRequest: starting timer for " + _pollingInterval + "ms" );
            _timer.start( 
                method( :onTimerMakeRequest ), 
                delay, 
                false 
            );
        } else {
            makeRequestPeriodic();
        }
    }
}