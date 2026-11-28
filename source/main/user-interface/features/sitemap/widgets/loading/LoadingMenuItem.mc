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

    /*
     * Debug-only: the loading entry stands in for content that is still being
     * built, so it is never reported as the focused element.
     */
    (:debug)
    public function focusTraceIsPlaceholder() as Boolean {
        return true;
    }
}
