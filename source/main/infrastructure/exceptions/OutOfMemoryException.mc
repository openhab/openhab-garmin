import Toybox.Lang;
import Toybox.Application;

/*
 * Exception thrown if there is not enough memory for an operation.
 */
class OutOfMemoryException extends GeneralException {
    function initialize() {
        GeneralException.initialize( Application.loadResource( Rez.Strings.ErrorOutOfMemory ) as String );
    }
}
