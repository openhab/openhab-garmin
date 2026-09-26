import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;
import Toybox.Time;

/*
 * A custom menu item that displays the age of the currently available
 * sitemap data, and provides an option to update it via WiFi.
 */
class SitemapRefreshMenuItem extends StructuredMenuItem {

    /*
    * Constructor.
    * Initializes the base class with the label.
    */
    public function initialize() {
        StructuredMenuItem.initialize( { 
            :label => "Update Sitemap\nover Wi-Fi",
            :allowLabelLineBreaks => true
        } );
    }

    // Trigger the sitemap sync
    public function onSelect() as Boolean {
        // Only if we are on WiFi connection we trigger the sync,
        // otherwise the menu item is disabled (see onUpdate)
        if( ConnectionManager.get().isWifiConnected() ) {
            // Request a sitemap update in sync mode from the sync delegate
            SitemapSyncDelegate.get().requestSitemapUpdate();
        }

        // True indicates that this function has handled the user input event
        return true;
    }

    // Called by the base class to render the menu item.
    public function onUpdate( dc as Dc ) as Void {
        if( ConnectionManager.get().isWifiConnected() ) {
            StructuredMenuItem.setLabelColor( getCurrentTheme().textColor );
            StructuredMenuItem.setActionIcon( ACTION_ICON_COMMAND );
        } else {
            StructuredMenuItem.setLabelColor( getCurrentTheme().stateColor );
            StructuredMenuItem.setActionIcon( null );
        }
        StructuredMenuItem.onUpdate( dc );
    }
}