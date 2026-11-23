import Toybox.Lang;
import Toybox.Communications;
import Toybox.PersistedContent;
import Toybox.WatchUi;

/*
    Base class for command requests
    Command requests are used to issue commands to openHAB items
    There are two derivates of this class, one for the custom Webhook,
    and one for the native REST APIs
    This class 
    - holds the menu item that this request is associated with
      - the menu item has to implement the CommandRequestDelegate,
        which prescribes event handlers for processing the result of the command
    - holds the URL for the request
    - provides a function for making the request, to be used by derivates
    - processes the response and calls the event handler of the item

    If the phone connection is not available, a (Wifi-based) sync is started with 
    `SyncDelegate` to send the command. Within the `SyncDelegate`, a separate
    command request is instantiated that operates in sync mode.
    
    In sync mode, sendCommand() follows different logic. The SitemapRequest
    is neither stopped nor started there, as this is handled by the
    CommandSyncDelegate. This ensures that the SitemapRequest is not active
    during sync and is stopped beforehand.
*/

// Interface to be implemented by menu items that issue commands.
// Defines the functions and callbacks required for interaction 
// of the command request with the menu item.
typedef CommandRequestDelegate as interface {
    function getItemName() as String;
    function onCommandComplete() as Void;
    function onCommandDeferredToSync() as Void;
    function onCommandException( ex as Exception ) as Void;
};

class BaseCommandRequest extends BaseRequest {
    
    // Depending on the settings, either a native REST API command request 
    // or a custom Webhook command request is instantiated.
    // If neither is configured, no command request is created, and items 
    // will only display their current state.
    public static function get( delegate as CommandRequestDelegate, syncMode as Boolean ) as BaseCommandRequest? {
        if( AppSettings.supportsRestApi() ) {
            return new NativeCommandRequest( delegate, syncMode );
        } else if( AppSettings.supportsWebhook() ) {
            return new WebhookCommandRequest( delegate, syncMode );
        }
        return null;
    }

    // We count the currently open requests for this item
    // If there is more than one, we cancel all requests in the
    // queue, to avoid BLE_QUEUE_FULL errors.
    // See makeWebRequest() for details.    
    private var _requestCounter as Number = 0;

    // True while a started request has not been cleaned up yet.
    // Used by finishRequest() to guarantee that the request counter
    // and the sitemap request are cleaned up exactly once, even if
    // the request fails synchronously and a callback arrives later.
    private var _requestStarted as Boolean = false;

    // The URL for the command request
    private var _url as String;
    
    // The item is kept as weak reference, to avoid
    // memory leaks due to circular references
    private var _weakItem as WeakReference;

    // True if the command request should operate in sync mode
    private var _syncMode as Boolean;

    // Item name and command value of the most recent request, used only by the
    // debug-only E2E trace. Both members are compiled only in debug builds.
    (:debug)
    private var _traceItem as String = "-";
    (:debug)
    private var _traceValue as String = "";

    // Constructor
    protected function initialize( 
        item as CommandRequestDelegate, 
        url as String, 
        method as Communications.HttpRequestMethod,
        syncMode as Boolean
    ) {
        BaseRequest.initialize( method );
        _weakItem = item.weak();
        _url = url;
        _syncMode = syncMode;
    }

    // Implemented by subclasses to provide the parameters for the web request.
    public function assembleParameters( cmd as String ) as Dictionary<String, Object> {
        throw new AbstractMethodException( "BaseCommandRequest.assembleParameters" );
    }

    // Sends the command via phone or Wifi connection
    // If not on Wifi connection or if in sync mode, the command will be sent immediately
    // If on Wifi connection, the command will be handed over to the sync delegate and
    // sync mode will be started.
    public function sendCommand( cmd as Item.ItemState ) as Void {

        // Non-string commands are converted to a string
        if( ! ( cmd instanceof String ) ) {
            cmd = cmd.toString();
        }
        traceCommandRequest( cmd as String );
        
        // Note that isWifiConnected()=true indicates only that Wi-Fi is available,
        // but the app still needs to switch into sync mode to connect to it. If _syncMode
        // is true, then we are already in sync mode and can send the command.
        if( ! ConnectionManager.get().isWifiConnected() || _syncMode ) {
            // Logger.debug( "BaseCommandRequest: sending command" );
            makeWebRequest( cmd );
        } else {

            // We are on WiFi connection, so we cannot send the command directly but
            // need to go into sync mode.
            
            var item = _weakItem.get() as CommandRequestDelegate?;
            if( item != null ) {
                
                // Logger.debug( "BaseCommandRequest: starting sync mode ..." );
                
                // We inform the item that the command has been deferred to sync mode
                // The item therefore does not need to (and should not) update
                // the state, since that is not displayed when on WiFi. Also the item must not call 
                // WatchUi.requestUpdate, which would interrupt the display of the sync mode.
                item.onCommandDeferredToSync();

                // Now we send the command via the sync delegate
                CommandSyncDelegate.get().sendCommand( [ item.getItemName(), cmd ] );

            } else {
                throw new GeneralException( "sendCommand: item reference is no longer valid" );
            }
        }
    }

    // Triggers the web request
    // @param parameters - options for the web request, as per Communication.makeWebRequest
    private function makeWebRequest( cmd as String ) as Void {
        // Logger.debug "BaseCommandRequest: makeWebRequest to " + _url );

        // Get the parameters from the subclass first. If this fails, no
        // request has been started and there is nothing to clean up.
        var parameters = assembleParameters( cmd ) as Dictionary<Object, Object>;

        // In sync mode, sendCommand will stop the requests, and they will be started 
        // after sync mode has been completed by a asynchronous task
        if( ! _syncMode ) {
            // Logger.debug( "BaseCommandRequest.makeWebRequest: stopping sitemap request" );
            SitemapRequest.get().beginCommand();
        }
        
        // If there is more than one open request for this item,
        // we cancel all requests to avoid -101/BLE_QUEUE_FULL errors.
        // This will also cancel any ongoing sitemap requests, which is acceptable.
        //
        // It may cancel commands for other items as well, but this is
        // highly unlikely—users typically can’t move between items
        // and trigger multiple commands quickly enough for this to be an issue.
        //
        // This situation mainly affects custom views. When commands are sent
        // from a menu item, the menu system blocks new commands until the
        // previous one has completed.
        if( _requestCounter > 0 ) {
            // Logger.debug( "BaseCommandRequest: cancelling previous requests!" );
            cancelAllRequests();
            _requestCounter = 0;
        }

        // From here on the request is considered started, so its cleanup
        // must happen even if Communications.makeWebRequest() throws
        // synchronously.
        _requestStarted = true;
        _requestCounter++;

        try {
            Communications.makeWebRequest( _url, parameters, getBaseOptions(), method( :onReceive ) );
        } catch( ex ) {
            // The request could not be made. Clean up the counter and command
            // mode before propagating the error.
            finishRequest();
            throw ex;
        }
    }

    // Cleans up a started command request.
    //
    // Decrements the request counter, and ends command mode so that sitemap
    // polling resumes. Must be called exactly once per started request,
    // regardless of whether a response arrived or the request failed.
    // Calling it more than once, or without a started request, is a no-op.
    private function finishRequest() as Void {
        if( ! _requestStarted ) {
            return;
        }
        // Clear the flag first, so that a reentrant call cannot clean up twice.
        _requestStarted = false;

        if( _requestCounter > 0 ) {
            _requestCounter--;
        }

        // In Wi-Fi sync mode, the sitemap request is restarted by a task
        // scheduled by the `SyncDelegate`.
        if( ! _syncMode ) {
            // Logger.debug( "BaseCommandRequest.finishRequest: restarting sitemap request" );
            SitemapRequest.get().endCommand();
        }
    }

    // Processes the response to the web request
    // If the request was successful, onCommandComplete() is called
    // If there was an error, onCommandException() is being called
    // The data type must match the callback signature of Communications.makeWebRequest exactly.
    public function onReceive( responseCode as Number, data as Dictionary or String or ByteArray or PersistedContent.Iterator or Null ) as Void {
        // Clean up the request before accessing the item: cleanup must not
        // depend on the item reference still being valid, otherwise the
        // request counter and sitemap polling could remain blocked.
        finishRequest();

        var item = _weakItem.get() as CommandRequestDelegate?;
        
        if( item == null ) {
            ExceptionHandler.handleBackgroundException(
                new GeneralException( "onReceive: item reference is no longer valid" )
            );
        } else {
            try {
                if( checkResponseCode( responseCode, CommunicationException.EX_SOURCE_COMMAND ) ) {
                    item.onCommandComplete();
                    traceCommandSuccess( responseCode );
                }
            } catch( ex ) {
                item.onCommandException( ex );
                traceCommandError( ex );
                // If we are in Wifi sync mode, we only report the exception to the
                // item (i.e. the CommandSyncDelegate)
                if( ! _syncMode ) {
                    ExceptionHandler.handleBackgroundException( ex );
                }
            }
        }
    }

    /*
     * Debug-only E2E traces for command request outcomes. The release
     * implementations are empty, so nothing is computed or printed in release
     * builds.
     */
    (:debug)
    private function traceCommandRequest( cmd as String ) as Void {
        var item = _weakItem.get() as CommandRequestDelegate?;
        _traceItem = item != null ? item.getItemName() : "-";
        _traceValue = cmd;
    }
    (:release)
    private function traceCommandRequest( cmd as String ) as Void {}

    (:debug)
    private function traceCommandSuccess( responseCode as Number ) as Void {
        E2ETrace.commandSucceeded( responseCode, _traceItem, _traceValue );
    }
    (:release)
    private function traceCommandSuccess( responseCode as Number ) as Void {}

    (:debug)
    private function traceCommandError( ex as Exception ) as Void {
        E2ETrace.commandFailed( E2ETraceExceptions.code( ex ), _traceItem, _traceValue );
    }
    (:release)
    private function traceCommandError( ex as Exception ) as Void {}
}