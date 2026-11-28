import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

class LoadingMenuItem extends StructuredMenuItem {
    
    // Constructor
    public function initialize() {
        StructuredMenuItem.initialize( {
            :label => Application.loadResource( Rez.Strings.Loading ) as String
        } );
    }

    /*
     * Debug-only E2E focus trace kind. Its label is translated chrome, so the
     * base class keeps the label empty and the trace carries no translated
     * text.
     */
    (:debug)
    public function focusTraceKind() as String {
        return "Loading";
    }
}
