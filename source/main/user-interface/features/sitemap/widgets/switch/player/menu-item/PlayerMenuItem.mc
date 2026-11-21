import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Menu item for Switch elements of type "Player".
 * Displays the current state as icons (play/pause).
 * When selected, opens a dedicated full-screen view with controls 
 * for "Play"/"Pause", "Next" and "Previous".
 */
class PlayerMenuItem extends SwitchMenuItem {

    // The full-screen view is instantiated only when the
    // menu item is selected
    private var _playerView as PlayerView?;

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
                :isActionable => true,
                :parent => parent,
                :processingMode => processingMode
            }
        );

        onStateChanged();
    }

    // Called by the delegate when the view is exited
    public function onReturn() as Void {
        _playerView = null;
    }

    // When the menu item is selected, either the full-screen view is initialized
    // and pushed onto the view stack, or, when in Wi-Fi mode, a command menu is
    // shown to let the user select one of the four available commands.
    public function onSelect() as Boolean {
        // First we see if the base class handles the event ...
        if( ! CommandMenuItem.onSelect() ) {
            // ... if not, and we do have a command request, then we
            // initialize a new full-screen view and display it
            if( hasCommandsEnabled() ) {
                var sitemapSwitch = getSitemapSwitch();
                if( sitemapSwitch.getSwitchItem().hasState() ) {
                    _playerView = new PlayerView( sitemapSwitch );
                    ViewStack.pushView(
                        _playerView,
                        new PlayerViewDelegate( self ),
                        WatchUi.SLIDE_LEFT
                    );
                } else {
                    CommandMenuHandler.showCommandSelection( 
                        sitemapSwitch.getLabel(),
                        [ ["Play", SwitchItem.ITEM_STATE_PLAY],
                          ["Pause", SwitchItem.ITEM_STATE_PAUSE],
                          ["Next", SwitchItem.ITEM_COMMAND_NEXT],
                          ["Previous", SwitchItem.ITEM_COMMAND_PREVIOUS] ],
                        self 
                    );
                }
            }
        }
        return true;
    }

    // The delegate uses this function to send play/pause commands
    // depending on the current state
    public function sendPlayPause() as Void {
        sendCommand(
            getSitemapSwitch().getSwitchItem().getState().equals( SwitchItem.ITEM_STATE_PLAY )
            ? SwitchItem.ITEM_STATE_PAUSE
            : SwitchItem.ITEM_STATE_PLAY
        );
    }

    /*
    * This function is used by both the constructor and updateWidget()
    * to refresh the displayed state.
    *
    * For PLAY and PAUSE, it uses PlayerMenuItemStateBitmap as the
    * state drawable of the base class.
    *
    * For all other states (typically NO_STATE), the state is shown
    * using the base class’s responsive state text.
    */
    public function onStateChanged() as Void {
        var sitemapSwitch = getSitemapSwitch();

        // If the view is currently open, we update it as well   
        if( _playerView != null ) {
            _playerView.updateWidget( sitemapSwitch );
        }

        var currentStateDrawable = getStateDrawable();

        // true for PLAY, false for PAUSE, null for any other state.
        // See derivePlayPauseState() for details.
        var isPlaying = derivePlayPauseState( sitemapSwitch );

        // For PLAY/PAUSE ...
        if( isPlaying != null ) {
            // ... we either update the existing Drawable or create a new one ...
            if( currentStateDrawable instanceof PlayerMenuItemStateBitmap ) {
                currentStateDrawable.setPlaying( isPlaying );
            } else {
                setStateDrawable( new PlayerMenuItemStateBitmap( isPlaying ) );
            }
            // ... and do not display a text.
            setStateTextResponsive( null );
        } else {
            // And for text we set the Drawable to null and instead
            // show the text
            setStateDrawable( null );
            setStateTextResponsive( sitemapSwitch.getDisplayState() );
        }
    }

    /*
     * The state icon is provided by the theme, so it is refreshed before
     * drawing. This also covers a night mode change that happens without
     * the playback state changing.
     */
    public function onUpdate( dc as Dc ) as Void {
        var stateDrawable = getStateDrawable();
        if( stateDrawable instanceof PlayerMenuItemStateBitmap ) {
            stateDrawable.updateTheme();
        }
        SwitchMenuItem.onUpdate( dc );
    }

    /*
     * Derives the playback state used to select the icon.
     *
     * Returns true for PLAY, false for PAUSE and null for any other state.
     *
     * If the raw state is neither PLAY nor PAUSE, the display state is
     * checked against the labels of the PLAY and PAUSE mappings. openHAB
     * reads the item state several times while it assembles the sitemap
     * response (once for the item data and again for the label), so the
     * label's state can briefly disagree with the item state. Preferring
     * the mapping label in that case prevents the mapping text (e.g. "||")
     * from being rendered in place of the icon.
     */
    private function derivePlayPauseState( 
        sitemapSwitch as SitemapSwitch 
    ) as Boolean? {
        var currentState = sitemapSwitch.getSwitchItem().getState();
        if( currentState.equals( SwitchItem.ITEM_STATE_PLAY ) ) {
            return true;
        } else if( currentState.equals( SwitchItem.ITEM_STATE_PAUSE ) ) {
            return false;
        }

        var commandDescriptions = sitemapSwitch.getCommandDescriptions();
        if( ! commandDescriptions.hasDescriptions() ) {
            return null;
        }

        // Labels may be empty for mappings without a label. In that case
        // the display state is of no use for determining the icon.
        var displayState = sitemapSwitch.getDisplayState();
        var pauseLabel = commandDescriptions.lookup( SwitchItem.ITEM_STATE_PAUSE );
        if( pauseLabel != null 
            && ! pauseLabel.equals( "" ) 
            && displayState.equals( pauseLabel ) 
        ) {
            return false;
        }

        var playLabel = commandDescriptions.lookup( SwitchItem.ITEM_STATE_PLAY );
        if( playLabel != null 
            && ! playLabel.equals( "" ) 
            && displayState.equals( playLabel ) 
        ) {
            return true;
        }

        return null;
    }

}