import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Menu item for Switch elements of type "Rollershutter".
 * Displays the current state as text, applying any available mappings and state descriptions.
 * When selected, opens a dedicated full-screen view with controls for "Up", "Down", and "Stop".
 */
class RollershutterMenuItem extends SwitchMenuItem {

    // The full-screen view is instantiated only when the
    // menu item is selected
    private var _rollershutterView as RollershutterView?;

    // Constructor
    // Initializes the BaseCommandRequest used for changing the state,
    // the Drawable for the displayed state and the base class
    public function initialize( 
        sitemapSwitch as SitemapSwitch,
        parent as BasePageMenu,
        processingMode as BasePageMenu.ProcessingMode
    ) {
        SwitchMenuItem.initialize( {
                :sitemapWidget => sitemapSwitch,
                :stateTextResponsive => sitemapSwitch.getDisplayState(),
                :isActionable => true,
                :parent => parent,
                :processingMode => processingMode
            }
        );
    }

    // Called by the delegate when the view is exited
    function onReturn() as Void {
        _rollershutterView = null;
    }

    // When the menu item is selected, the full-screen 
    // view is initialized and pushed to the view stack
    public function onSelect() as Boolean {
        // First we see if the base class handles the event ...
        if( ! SwitchMenuItem.onSelect() ) {
            // ... if not, and we do have a command request, then we
            // initialize a new full-screen view and display it
            if( hasCommandsEnabled() ) {
                _rollershutterView = new RollershutterView( getSitemapSwitch() );
                ViewStack.pushView(
                    _rollershutterView,
                    new RollershutterViewDelegate( self ),
                    WatchUi.SLIDE_LEFT
                );
            }
        }
        return true;
    }

    // Overrides the base class update method to refresh the displayed
    // text state.
    // We intentionally do not implement onStateChanged(), since this
    // menu item only reacts to display state updates originating from
    // the server.
    // For Rollershutter, the commands sent (UP/DOWN/STOP) are not
    // reflected in the item state. Therefore, no local state update
    // should be applied when a command is issued.
    public function updateWidget( sitemapWidget as SitemapWidget ) as Void {
        WidgetMenuItem.updateWidget( sitemapWidget );
        
        // Update the display state
        setStateTextResponsive( sitemapWidget.getDisplayState() );
        
        // If the view is currently open, we update it as well      
        if( _rollershutterView != null ) {
            _rollershutterView.updateWidget( getSitemapSwitch() );
        }
    }
}