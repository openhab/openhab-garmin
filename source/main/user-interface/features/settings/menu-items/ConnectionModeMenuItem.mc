import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * A custom menu item that displays the current connectivity mode.
 */
class ConnectionModeMenuItem extends LabelMenuItem {

    /*
    * Constructor.
    * Initializes the base class with the label and sublabel.
    */
    public function initialize() {
        LabelMenuItem.initialize( 
            Application.loadResource( Rez.Strings.SettingsConnectivityMode ) as String,
            Application.loadResource( Rez.Strings.NotAvailable ) as String
        );
    }

    /*
    * Called by the base class to handle drawing.
    * Also updates the connectivity mode.
    */
    public function onUpdate( dc as Dc ) as Void {
        setSubLabel( ConnectionManager.get().getStateDescription() );
        LabelMenuItem.onUpdate( dc );
    }
}