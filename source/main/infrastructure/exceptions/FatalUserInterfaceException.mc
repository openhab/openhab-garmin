import Toybox.Lang;
import Toybox.Application;

/*
 * Class for fatal user interface exceptions that should be displayed
 * to the user as full-screen error.
 */
class FatalUserInterfaceException extends GeneralException {
    function initialize( ex as Exception ) {
        var errorMessage = ex.getErrorMessage();
        GeneralException.initialize( Lang.format(
            Application.loadResource( Rez.Strings.ErrorFatalUi ) as String,
            [ errorMessage != null ? errorMessage : "" ]
        ) );
    }
}
