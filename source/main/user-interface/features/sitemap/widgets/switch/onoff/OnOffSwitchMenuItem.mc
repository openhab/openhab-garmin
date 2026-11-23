import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * A menu item that displays an on/off switch as its state.
 * Selecting the item toggles the switch state, or displays
 * a command selection menu if the current state is unknown.
 *
 * This menu item is only used for Switch and Dimmer items. Those are the
 * only item types whose state can be interpreted as binary without losing
 * information. See MenuItemFactory.getSwitchMenuItemType().
 */
class OnOffSwitchMenuItem extends SwitchMenuItem {

    // True if the switch is on
    private var _isEnabled as Boolean?;

    // True if the widget has nested elements and
    // thus a smaller icon shall be displayed
    private var _smallIcon as Boolean;

    // The actual Drawable for drawing the switch
    private var _stateDrawable as OnOffStateDrawable;

    // Constructor
    public function initialize( 
        sitemapSwitch as SitemapSwitch,
        parent as BasePageMenu,
        processingMode as BasePageMenu.ProcessingMode
    ) {
        _isEnabled = parseItemState( sitemapSwitch.getSwitchItem().getState() );

        _smallIcon = sitemapSwitch.getLinkedPage() != null;
        _stateDrawable = new OnOffStateDrawable( _isEnabled, isFocused(), _smallIcon );
        
        // Initialize the base class
        // For the toggle switch we support the display
        // of a display state, if provided by the server
        SwitchMenuItem.initialize( {
                :sitemapWidget => sitemapSwitch,
                :stateTextResponsive => deriveTextState( sitemapSwitch ),
                :stateDrawable => _stateDrawable,
                :isActionable => false,
                :parent => parent,
                :processingMode => processingMode
            }
        );
    }

    // Returns the display text for a toggle switch based on the given
    // `SitemapSwitch` data structure.
    //
    // The text is rendered in front of the toggle switch.
    // Dimmers are handled separately and rendered manually to ensure
    // consistent appearance across the app.
    private static function deriveTextState( sitemapSwitch as SitemapSwitch ) as String? {
        var switchItem = sitemapSwitch.getSwitchItem();
        return
            switchItem.getType().equals( "Dimmer" )
            ? switchItem.getState().equals( "0" ) || ! switchItem.hasState()
                ? null
                : sitemapSwitch.getDisplayState()
            : sitemapSwitch.getRemoteDisplayStateOrNull();
    }

    // If a current state is available, the state is toggled and the
    // corresponding command is returned.
    // If no state is available, a command selection menu (ON/OFF) is shown
    // instead.
    public function onSelect() as Boolean {
        if( ! SwitchMenuItem.onSelect() && ! hasPendingCommand() ) {
            if( _isEnabled != null ) {
                sendCommand( 
                    _isEnabled 
                    ? SwitchItem.ITEM_STATE_OFF 
                    : SwitchItem.ITEM_STATE_ON 
                );
            } else {
                CommandMenuHandler.showCommandSelection( 
                    getSitemapSwitch().getLabel(),
                    [ [SwitchItem.ITEM_STATE_ON, SwitchItem.ITEM_STATE_ON],
                    [SwitchItem.ITEM_STATE_OFF, SwitchItem.ITEM_STATE_OFF] ], 
                    self 
                );
            }
        }
        return true;
    }

    // Called by the base class when the state changes, either due to
    // a sitemap update from the server or a local change after a
    // command was sent.
    // Updates the locally stored state and the associated Drawable.
    // Calling WatchUi.requestUpdate() is handled by the base class.
    public function onStateChanged() as Void {
        SwitchMenuItem.onStateChanged();
        _isEnabled = parseItemState( getSitemapSwitch().getSwitchItem().getState() );
        _stateDrawable.setEnabledAndIconSize( _isEnabled, _smallIcon );
    }

    // To apply the correct color theme, the Drawable representing the
    // toggle switch must know whether the menu item is currently selected.
    // Since the selection state can change at any time, we override
    // onUpdate() and update the focus state on every call.
    public function onUpdate( dc as Dc ) as Void {
        _stateDrawable.updateTheme( isFocused() );
        SwitchMenuItem.onUpdate( dc );
    }

    // Converts the string state to a nullable Boolean for _isEnabled
    // The widget supports the string states of Switch items:
    // "ON" => true; "OFF" => false
    // And the numeric states of Dimmer items:
    // 0 => false; 1-100 => true
    // The numeric handling is only valid because this menu item is restricted
    // to Switch and Dimmer items, see MenuItemFactory.getSwitchMenuItemType().
    // If the state is NO_STATE, then null will be returned.
    private function parseItemState( itemState as String ) as Boolean? {
        if( itemState.equals( Item.NO_STATE ) ) {
            return null;
        } else if( itemState.equals( SwitchItem.ITEM_STATE_ON ) ) {
            return true;            
        } else if( itemState.equals( SwitchItem.ITEM_STATE_OFF ) ) {
            return false;
        } else {
            var numericState = itemState.toNumber();
            if( numericState != null ) {
                if( numericState == 0 ) {
                    return false;
                } else if( numericState > 0 && numericState <= 100 ) {
                    return true;
                }
            }
        }
        throw new GeneralException( Lang.format(
            Application.loadResource( Rez.Strings.ErrorUnsupportedState ) as String,
            [ itemState ]
        ) );
    }

    // Overrides the base class update method to refresh the displayed
    // text state.
    // This logic is implemented here rather than in onStateChanged(),
    // because the display text may change even when the underlying item
    // state remains the same (e.g., for group items showing the number
    // of active members).
    public function updateWidget( sitemapWidget as SitemapWidget ) as Void {
        SwitchMenuItem.updateWidget( sitemapWidget );

        _smallIcon = sitemapWidget.getLinkedPage() != null;
        
        setStateTextResponsive( deriveTextState( getSitemapSwitch() ) );
    }
}