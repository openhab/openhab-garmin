import Toybox.Lang;
import Toybox.Communications;

/*
 * Exception for any non-200 response codes from `Communications.makeWebRequest`.
 * Positive codes represent HTTP state codes (e.g., 404 for Not Found).
 * Negative codes represent Garmin SDK errors (e.g., –400 for unexpected response content type).
 */
class CommunicationException extends CommunicationBaseException {

    // The response code from Communications.makeWebRequest
    private var _responseCode as Number;
    public function getResponseCode() as Number {
        return _responseCode;
    }

    // Constructor
    public function initialize( code as Number, source as CommunicationBaseException.Source ) {
        CommunicationBaseException.initialize( source );
        _responseCode = code;
    }

    // Get the long error message
    public function getErrorMessage() as String or Null {
        var errorMsg;
        // For some errors we return specific messages,
        // for all others a generic one
        if( isNoPhone() && ConnectionManager.get().isWifiConnected() ) {
            return "Wi-Fi connection lost";
        } else if( isNoPhone() ) {
            return "No phone\n(" + _responseCode + ")";
        } else if ( _responseCode == Communications.INVALID_HTTP_BODY_IN_NETWORK_RESPONSE ) {
            errorMsg = "Invalid response\n(" + _responseCode + ")";

        } else if ( 
            _responseCode == Communications.NETWORK_RESPONSE_TOO_LARGE
            || _responseCode == Communications.NETWORK_RESPONSE_OUT_OF_MEMORY
        ) {
            return "Sitemap too large\n(" + _responseCode + ")";
        
        } else {
            errorMsg = "Request failed\n(" + _responseCode + ")";
        }
        
        return getSourceName() + ":\n" + errorMsg;
    }

    // Toast message
    public function getToastMessage() as String {
        var errorMsg;
        // For some errors we return specific toast messages,
        // for all others a generic one
        if( isNoPhone() && ConnectionManager.get().isWifiConnected() ) {
            return "No Wi-Fi";
        } else if( isNoPhone() ) {
            return "No phone";
        } else if ( _responseCode == Communications.INVALID_HTTP_BODY_IN_NETWORK_RESPONSE ) {
            errorMsg = "INVRES";
        } else {
            errorMsg = _responseCode.toString();
        }
        
        return getSourceShortCode() + ":" + errorMsg;
    }

    // Most response codes do not require a full-screen error,
    // with a few exceptions
    public function requiresFullScreenError() as Boolean {
        // return false; // activate this to use -1001 in simulator to test toast notifications
        return 
            _responseCode == Communications.SECURE_CONNECTION_REQUIRED
            || _responseCode == Communications.NETWORK_RESPONSE_TOO_LARGE
            || _responseCode == Communications.NETWORK_RESPONSE_OUT_OF_MEMORY
            || _responseCode == 404; 
    }

    // True if the exception indicates that no phone is present
    public function isNoPhone() as Boolean {
        return 
             _responseCode == Communications.BLE_CONNECTION_UNAVAILABLE 
            || _responseCode == Communications.BLE_HOST_TIMEOUT; 
    }

    // A -400 response code is one way an empty response from myopenhab.org 
    // may present itself. So in this case, this function returns true, allowing 
    // the error to be suppressed if the corresponding settings option is enabled.
    public function suppressAsEmptyResponse() as Boolean {
        return 
            _responseCode == Communications.INVALID_HTTP_BODY_IN_NETWORK_RESPONSE
            && isFrom( CommunicationBaseException.EX_SOURCE_SITEMAP );
    }
}