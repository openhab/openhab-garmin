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
 *     - stop(): halts all further web requests.
 *       If a request is already in progress, its response will be ignored after stop() is called.
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
    
    // Members for controlling the behavior when stopped.
    // if _stopCount is > 0, no further requests will be allowed
    // Each stop will increase the stop count, and only after
    // one start was called for each stop, requests are continued
    // We start stopped, so the initial value is 1
    private var _stopCount as Number = 1;

    // _hasPendingRequest is true if we are inbetween a makeRequest()
    // and an onReceive
    private var _hasPendingRequest as Boolean = false;
    // If stop() is called and there is a pending request, this
    // is set to true to instrucht onReceive() to ignore the
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
        // - we are not stopped
        // - or it is a single request, which does not the timer to be started
        // - and there is no pending request
        if( ( _stopCount <= 0 || singleRequest ) && ! _hasPendingRequest ) {
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

        // When stop() is called, and there is a pending request, then
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

    // Start the request loop
    public function start() as Void {
        // Logger.debug( "SitemapRequest.start" );
        if( _stopCount <= 0 ) {
            throw new GeneralException( "Tried to start already running sitemap request" );
        } else {
            _stopCount--;
            // Logger.debug( "SitemapRequest.start: new count=" + _stopCount );
        }
        if( _stopCount == 0 ) {
            // Logger.debug( "SitemapRequest.start: making request" );
            makeRequestPeriodic();
        }
    }

    // Stops the request loop
    // If there is a pending request, onReceive() is instructed to
    // ignore the next response
    public function stop() as Void {
        // Logger.debug( "SitemapRequest.stop" );
        _stopCount++;
        // Stop a pending timer, so that no request callback fires while
        // the request loop is stopped (e.g. during app shutdown)
        _timer.stop();
        // When the SitemapRequest is stopped, all ongoing asynchronous
        // processing is also halted. Tasks in the task queue are atomic
        // in the sense that stopping between tasks will not cause any
        // data inconsistencies.
        AsyncTaskQueue.get().removeAll();
        if( _hasPendingRequest ) {
            // Logger.debug( "SitemapRequest.stop: pending request, will ignore the next response" );
            _ignoreNextResponse = true;
        }
    }

    // Used to trigger the next request after the current response has been
    // successfully processed.
    public function triggerNextRequest( applyErrorMinimumInterval as Boolean ) as Void {
        // Logger.debug( "SitemapRequest.triggerNextRequestInternal" );

        // Do not schedule requests while the loop is stopped, e.g. when a
        // response arrives after stop() was called during app shutdown
        if( _stopCount > 0 ) {
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