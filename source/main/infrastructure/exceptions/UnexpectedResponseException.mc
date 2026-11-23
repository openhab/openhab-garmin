import Toybox.Lang;
import Toybox.Application;

/*
 * Exception thrown when a web request returns HTTP 200 but the response data
 * is not of the expected type.
 *
 * Note: behavior varies across devices—some Garmin units or paired phones
 * report SDK error –400 for unexpected data, while others return 200 with
 * an empty (null) response.
 */
class UnexpectedResponseException extends CommunicationBaseException {

    private var _data as Object?;

    public function initialize( data as Object?, source as CommunicationBaseException.Source ) {
        CommunicationBaseException.initialize( source );
        _data = data;
    }

    public function getErrorMessage() as String or Null {
        return getSourceName() + ": " + (
            _data == null ?
            Application.loadResource( Rez.Strings.ErrorEmptyResponseDetail ) as String :
            Lang.format(
                Application.loadResource( Rez.Strings.ErrorUnexpectedResponseDetail ) as String,
                [ _data ]
            )
            );
    }

    public function getToastMessage() as String {
        return getSourceShortCode() + ":" + (
            _data == null ?
            Application.loadResource( Rez.Strings.ErrorCodeEmptyResponse ) as String :
            Application.loadResource( Rez.Strings.ErrorCodeUnexpectedResponse ) as String
            );
    }

    // A null result is one way an empty response from myopenhab.org may 
    // present itself. So if data is null, this function returns true, allowing 
    // the error to be suppressed if the corresponding settings option is enabled.
    public function suppressAsEmptyResponse() as Boolean {
        return _data == null;
    }
}