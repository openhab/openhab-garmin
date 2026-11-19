import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * A menu item that displays the current state of an item as text
 * and sends a command when selected.
 *
 * Intended for use with `Switch` sitemap elements. The available commands
 * are defined via the element's `mappings` property or via command
 * descriptions on the item.
 *
 * Behavior on selection:
 * - If only one command is defined, it is sent immediately.
 * - If two commands are defined and the current state is known and 
 *   matches one of them, the other command is sent (toggling behavior).
 * - In all other cases, an command selection menu is shown, allowing the user to
 *   manually select a command to send.
 */
class GenericSwitchMenuItem extends SwitchMenuItem {
    
    // Returns true if the given widget matches the type handled by this menu item.
    public static function isMyType( sitemapWidget as SitemapWidget ) as Boolean {
        // This menu item applies to all Switches with available commands,
        // unless the available commands represent an ON/OFF toggle. Player and
        // Rollershutter items use their dedicated menu items, even though they
        // may define mappings as well.
        return 
            sitemapWidget instanceof SitemapSwitch 
            && sitemapWidget.hasMappings()
            && ! sitemapWidget.hasToggleSwitchMappings()
            && ! sitemapWidget.getSwitchItem().getType().equals( "Rollershutter" )
            && ! sitemapWidget.getSwitchItem().getType().equals( "Player" );
    }

    // Constructor
    public function initialize( 
        sitemapSwitch as SitemapSwitch,
        parent as BasePageMenu,
        processingMode as BasePageMenu.ProcessingMode
    ) {
        // Initialize the base class
        SwitchMenuItem.initialize( {
                :sitemapWidget => sitemapSwitch,
                :stateTextResponsive => sitemapSwitch.getDisplayState(),
                :isActionable => true,
                :parent => parent,
                :processingMode => processingMode
            }
        );
    }

    // Called by the base class when the state changes, either due to
    // a sitemap update from the server or a local change after a
    // command was sent.
    // Updates the locally stored state and the associated Drawable.
    // Calling WatchUi.requestUpdate() is handled by the base class.
    public function onStateChanged() as Void {
        SwitchMenuItem.onStateChanged();
        setStateTextResponsive( getSitemapSwitch().getDisplayState() );
    }

    // Either directly sends a command, or if presents the user with a
    // command selection menu. 
    // See the class-level comment for details on the applied logic.
    public function onSelect() as Boolean {
        if( ! SwitchMenuItem.onSelect() && ! hasPendingCommand() ) {
            var sitemapSwitch = getSitemapSwitch();
            var switchItem = sitemapSwitch.getSwitchItem();
            var hasState = switchItem.hasState(); 
            var itemState = switchItem.getState();
            var commandDescriptions = sitemapSwitch.getCommandDescriptions();

            if( commandDescriptions.size() == 1 ) {
                // For one mapping, we just send that command
                sendCommand( commandDescriptions.getCommandDescription( 0 ).getCommand() );
                return true;
            } else if( commandDescriptions.size() == 2 && hasState ) {
                // For two mappings, we check if the current state equals
                // to one of them and then send the other
                var firstCommand = commandDescriptions.getCommandDescription( 0 ).getCommand();
                var secondCommand = commandDescriptions.getCommandDescription( 1 ).getCommand();
                if( firstCommand.equals( itemState ) ) {
                    sendCommand( secondCommand );
                    return true;
                } else if( secondCommand.equals( itemState ) ) {
                    sendCommand( firstCommand );
                    return true;
                }
            }

            // For all other cases, use the base class function to show a menu that allows the
            // user to select a command. On most devices, an ActionMenu is used. On Edge
            // devices, where ActionMenu is not available, the CommandMenu implementation
            // (a CustomMenu, as used for the sitemap) is used instead.

            // Assemble the list of menu entries ...
            var menuEntries = [];
            for( var i = 0; i < commandDescriptions.size(); i++ ) {
                var commandDescription = commandDescriptions.getCommandDescription( i );
                var command = commandDescription.getCommand();
                // We exclude the current state
                if( ! ( hasState && command.equals( itemState ) ) ) {
                    menuEntries.add( [commandDescription.getLabel(), command] );
                }
            }
            
            // ... and show the menu
            CommandMenuHandler.showCommandSelection( 
                getSitemapSwitch().getLabel(), 
                menuEntries, 
                self 
            );
        }

        return true;
    }

}