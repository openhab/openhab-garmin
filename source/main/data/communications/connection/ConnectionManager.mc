import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Communications;
import Toybox.Time;

/*
 * This singleton class keeps track of the current connectivity state.
 *
 * Connectivity is driven by sitemap requests. On startup, the
 * ConnectionManager assumes that a phone connection is available
 * (BLUETOOTH_CONNECTED).
 *
 * If a sitemap request fails because the phone is not connected,
 * the SitemapRequest asks the ConnectionManager to check WiFi
 * availability. The connectivity state then transitions to
 * WIFI_CHECK_PENDING.
 *
 * If WiFi is available, the state moves to WIFI_CONNECTED.
 * Otherwise, it transitions to OFFLINE.
 *
 * The sitemap request continues to run and updates the connectivity
 * state accordingly.
 *
 * The Wi-Fi check is handled differently. Because it is relatively
 * expensive, it is performed only once when Bluetooth is not available.
 * After that, the state remains WIFI_CONNECTED until one of the
 * following occurs:
 *
 * - A SitemapRequest succeeds via Bluetooth
 *   => state transitions to BLUETOOTH_CONNECTED
 *
 * - A manually triggered command sent via Wi-Fi fails
 *   => state transitions to OFFLINE
 */
 public class ConnectionManager {

    /******* STATIC *******/ 

    // Singleton instance and accessor
    private static var _instance as ConnectionManager?;

    public static function get() as ConnectionManager {
        if( _instance == null ) {
            _instance = new ConnectionManager();
        }
        return _instance as ConnectionManager;
    }

    /******* INSTANCE *******/ 

    // The four connectivity states
    public enum State {
        BLUETOOTH_CONNECTED,
        // WIFI_CHECK_PENDING is only used for the transition from
        // BLUETOOTH_CONNECTED to WIFI_CONNECTED or OFFLINE
        // If the current state is OFFLINE, it stays OFFLINE until
        // a Wi-Fi connection can be confirmed.
        WIFI_CHECK_PENDING,
        WIFI_CONNECTED,
        OFFLINE
    }

    // The Dictionary type passed to the callback when checking
    // for WiFi connection
    typedef TryWifiResult as { 
        :wifiAvailable as Lang.Boolean, 
        :errorCode as Communications.WifiConnectionStatus 
    };

    // The current state
    private var _state as State = BLUETOOTH_CONNECTED;

    // The timestamp of the last successful Wifi check or phone connection
    private var _lastSuccessfullConnection as Moment?;
    
    // Only needed to be declared private to prevent other classes
    // from instantiating this singleton
    private function initialize() {}

    // This is called by `SitemapRequest` and `ErrorHandler` to indicate that a phone
    // connection is currently available
    public function confirmPhoneConnection() as Void {
        // Logger.debug( "ConnectionManager.confirmPhoneConnection" );
        updateStateAndUi( BLUETOOTH_CONNECTED );
        _lastSuccessfullConnection = Time.now();
    }

    // Returns the state
    public function getState() as State {
        return _state;
    }

    // Returns a textual description of the current state
    public function getStateDescription() as String {
        switch( _state ) {
            case BLUETOOTH_CONNECTED: return "Phone (BLE)";
            case WIFI_CHECK_PENDING: return "Checking Wi-Fi availablity ...";
            case WIFI_CONNECTED: return "Wi-Fi";
            case OFFLINE: return "Offline";
            default: return "Invalid";
        }
    }

    // Returns true if last successful phone connection or Wifi check 
    // happened within the state expiry time. While states are not acutally shown
    // in Wifi mode, we still want to use the same timeframe
    // to go into offline mode if Wifi connection is lost.
    public function hadSuccessfulConnectionWithinLimit() as Boolean {
        return 
            _lastSuccessfullConnection != null
            && Time.now().compare( _lastSuccessfullConnection ) < Config.STATE_EXPIRATION_TIME;
    }

    // Returns true if the device settings show that there is Wi-Fi capability
    public function hasWifiCapability() as Boolean {
        var wifi = System.getDeviceSettings().connectionInfo[:wifi];
        return wifi != null && wifi.state != System.CONNECTION_STATE_NOT_INITIALIZED;
    }

    // True if the Wi-Fi check is pending
    public function hasWifiCheckPending() as Boolean {
        return _state == WIFI_CHECK_PENDING;
    }

    // True if any connection is available
    public function isConnected() as Boolean {
        return isBluetoothConnected() || isWifiConnected();
    }

    // True if no connection is available
    public function isOffline() as Boolean {
        return _state == OFFLINE;
    }

    // True if phone connection is available
    public function isBluetoothConnected() as Boolean {
        return _state == BLUETOOTH_CONNECTED;
    }

    // Returns true if the device settings indicate that a phone is connected.
    // The internal state exposed by isBluetoothConnected() is updated only
    // after the phone connection has been confirmed by a successful
    // sitemap request.
    public function isPhoneConnectedAccordingToSettings() as Boolean {
        var bluetooth = System.getDeviceSettings().connectionInfo[:bluetooth];
        return bluetooth != null && bluetooth.state == System.CONNECTION_STATE_CONNECTED;
    }

    // True if no phone connection is available
    // but WiFi is
    public function isWifiConnected() as Boolean {
        return _state == WIFI_CONNECTED;
    }

    // This is called by `SitemapRequest` when there is no phone connection available 
    // and triggers a check of WiFi availability. After the check is concluded
    // SitemapRequest.triggerNextRequest
    public function tryWifiConnectionAndTriggerNextRequest() as Void {
        // Logger.debug( "ConnectionManager.tryWifiConnectionAndTriggerNextRequest" );
        
        // Check first if the devices has Wi-Fi capability, and
        // if not, go directly into offline mode
        if( ! hasWifiCapability() ) {
            updateStateAndUi( OFFLINE );
            SitemapRequest.get().triggerNextRequest( true );
        } else if( _state != WIFI_CONNECTED || BaseSyncDelegate.consumeWifiCheckRequest() ) {
            // Since the Wi-Fi check is relatively time-consuming and blocks Wi-Fi sync,
            // meaning no commands can be sent via Wi-Fi while it is running,
            // we perform the check only once, or if requested by a sync delegate after
            // a failed sync.

            // Logger.debug( "ConnectionManager.tryWifiConnectionAndTriggerNextRequest: checking for Wi-Fi connection" );
            
            // WIFI_CHECK_PENDING is only used for the transition from
            // BLUETOOTH_CONNECTED to other states
            if( _state == BLUETOOTH_CONNECTED ) {
                updateStateAndUi( WIFI_CHECK_PENDING );
            }
            Communications.checkWifiConnection( method( :processWifiCheckResponseAndTriggerNextRequest ) );
        } else {
            // If SitemapRequest triggers a Wi-Fi check, it does NOT schedule
            // the next execution itself. Scheduling is handled here, or
            // after the Wi-Fi check has completed.
            SitemapRequest.get().triggerNextRequest( true );
        }
    }

    // Processes the result of the WiFi availability check.
    // Depending on the outcome, the displayed view is updated
    // and sitemap states are invalidated if necessary.
    public function processWifiCheckResponseAndTriggerNextRequest( result as TryWifiResult ) as Void {
        // Logger.debug( "ConnectionManager.processWifiCheckResponseAndTriggerNextRequest" );
        try {
            // If since the request was made the state has been changed
            // back to BLUETOOTH_CONNECTED, we ignore the result
            if( _state != BLUETOOTH_CONNECTED && result[:wifiAvailable] == true ) {
                // If WiFi is available ...
                _lastSuccessfullConnection = Time.now();
                // ... we update the state.
                updateStateAndUi( WIFI_CONNECTED );
            } else {
                updateStateAndUi( OFFLINE );
            }
        } catch( ex ) {
            ExceptionHandler.handleBackgroundException( ex );
        } finally {
            // When SitemapRequest initiates a Wi-Fi check it DOES NOT
            // schedule the next execution, this is done here, only
            // after the Wi-Fi check was completed.
            SitemapRequest.get().triggerNextRequest( true );
        }
    }

    // Internal function that updates the connectivity state.
    //
    // Besides storing the new state, it invalidates the display states
    // when entering Wi-Fi check or offline mode, requests the sitemap
    // via Wi-Fi if required, and triggers a UI refresh.
    private function updateStateAndUi( newState as State ) as Void {
        var oldState = _state;
        if( oldState != newState ) {
            _state = newState;

            // When returning to phone mode, the states had been hidden before,
            // so the next response must be applied even if its JSON is
            // unchanged, to bring them back.
            if( newState == BLUETOOTH_CONNECTED ) {
                SitemapStore.noteModelDivergence();
            }

            // When leaving phone mode and a HomepageMenu is available,
            // i/nvalidate the current states even if they are still fresh.
            // States must not be displayed while in Wi-Fi or offline mode,
            // even when the underlying sitemap is up to date.
            if( oldState == BLUETOOTH_CONNECTED
                && HomepageMenu.exists()
                && SitemapStore.isSitemapFresh() 
            ) {
                HomepageMenu.get().hideStates();
                // hideStates updates the menu structure asynchronously.
                // so we need to schedule an asynchronous task to request
                // an UI update afterwards.
                AsyncTaskQueue.get().add( new RequestUiUpdateTask() );
            }
            // If no menu is available while in Wi-Fi mode, this indicates that
            // no sitemap is stored locally and it must be requested via Wi-Fi
            // using the sync delegate.
            else if( _state == WIFI_CONNECTED && ! HomepageMenu.exists() ) {
                // Logger.debug( "ConnectionManager: on Wi-Fi but no sitemap, requesting an update." );
                
                // If this happens on startup, then OHApp already pushed
                // the loading view and we just need to request the sitemap
                // via WiFi
                if( LoadingView.isShowing() ) {
                    SitemapSyncDelegate.get().requestSitemapUpdate();
                } 
                // If there is no loading view shown, we push the loading view
                // first and instruct it via the constructor parameter to then
                // trigger the WiFi sync once it is loaded. It is not possible
                // to push the loading view AND request the WiFi sync at the same
                // time, at least in the simulator this will lead to a crash
                else {
                    ViewStack.resetTo( new LoadingView( true ), null );
                }
            }
            
            // As final action we update the screen to reflect the new
            // state, except if we are offline and there is no HomepageMenu,
            // in which case we switch to an error view.
            if( _state == OFFLINE && ! HomepageMenu.exists() ) {
                ExceptionHandler.showOrDefer( new OfflineWithoutSitemapException() );
            } else {
                // Redraw the connection mode indicators
                ConnectionModeIndicator.update();
                
                // Request an immediate UI update
                // to refresh the connection mode indicators
                WatchUi.requestUpdate();
            }
        }
    }
}