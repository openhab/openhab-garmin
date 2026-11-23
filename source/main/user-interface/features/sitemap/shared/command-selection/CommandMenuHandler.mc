import Toybox.Lang;
import Toybox.WatchUi;

// This class provides showCommandSelection, a static function that displays a menu
// allowing the user to choose from a list of commands. It is used for generic
// switches with more than two states, as well as for two-state switches when the
// current state is unknown and toggling is therefore not possible.
//
// There are two implementations:
// - ActionMenu on devices that support it
// - CustomMenu on devices where ActionMenu is not available (e.g. Edge devices)
    
// Types for passing in data to showCommandSelection
typedef CommandMenuEntry as [String, String];
typedef CommandMenuEntries as Array<CommandMenuEntry>;

// Sending the actual command done by a delegate passed into
// showCommandSelection, which needs to fullfil this interface
typedef SendCommandDelegate as interface {
    function sendCommand( cmd as Item.ItemState ) as Void;
    function sendCommandAndLeaveView( cmd as Item.ItemState ) as Void;
};

class CommandMenuHandler {
    // ActionMenu implementation
    (:exclForFullMenu)
    public static function showCommandSelection( 
        label as String,
        menuEntries as CommandMenuEntries, 
        delegate as SendCommandDelegate 
    ) as Void {
        var actionMenu = new ActionMenu( null );
        for( var i = 0; i < menuEntries.size(); i++ ) {
            actionMenu.addItem( 
                new ActionMenuItem( { 
                    :label => menuEntries[i][0] }, 
                    menuEntries[i][1] 
            ) );
        }
        WatchUi.showActionMenu( actionMenu, new CommandActionMenuDelegate( delegate ) );
        traceActionMenu();
    }

    // Debug-only E2E trace for the command ActionMenu, which is shown outside
    // the ViewStack and therefore sits one level above the current app view.
    (:debug)
    private static function traceActionMenu() as Void {
        E2ETrace.actionMenuShown( ViewStack.getDepth() + 1 );
    }
    (:release)
    private static function traceActionMenu() as Void {}

    // CustomMenu implementation
    (:exclForActionMenu)
    public static function showCommandSelection( 
        label as String,
        menuEntries as CommandMenuEntries, 
        delegate as SendCommandDelegate 
    ) as Void {
        // Instantiate the menu
        var menu = new CommandMenu( label );
        for( var i = 0; i < menuEntries.size(); i++ ) {
            menu.addItem( 
                new CommandOptionMenuItem( 
                    menuEntries[i][0], 
                    menuEntries[i][1] 
            ) );
        }
        ViewStack.pushView( menu, new CommandMenuDelegate( delegate ), WatchUi.SLIDE_LEFT );
    }
}
