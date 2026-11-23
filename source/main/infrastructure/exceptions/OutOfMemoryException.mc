import Toybox.Lang;
import Toybox.Application;

/*
 * Exception thrown if there is not enough memory for an operation.
 */
class OutOfMemoryException extends GeneralException {
    function initialize( freeMemory as Number, requiredMemory as Number ) {
        GeneralException.initialize( Lang.format(
            Application.loadResource( Rez.Strings.ErrorOutOfMemory ) as String,
            [ freeMemory, requiredMemory ]
        ) );
    }
}
