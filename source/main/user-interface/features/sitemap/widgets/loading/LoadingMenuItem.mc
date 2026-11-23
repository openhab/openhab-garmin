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
}