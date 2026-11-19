import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Input delegate for the `CommandMenu`.
 *
 * Once a command is selected, this delegate sends it via the
 * associated menu item.
 *
 * This delegate used only on Edge devices, where the `ActionMenu` is not available.
 */
(:exclForActionMenu)
class CommandMenuDelegate extends Menu2InputDelegate {

    var _delegate as SendCommandDelegate;

    // Constructor
    public function initialize( delegate as SendCommandDelegate ) {
        Menu2InputDelegate.initialize();
        _delegate = delegate;
    }

    // On select, send the command
    public function onSelect( item as MenuItem ) as Void {
        // Logger.debug "CommandMenuDelegate.onSelect" );
        try {
            // The action menu items have the command as Id
            // var command = item.getId();
            var command = ( item as CommandOptionMenuItem ).getCommand();
            if( command instanceof String ) {
                // Leave the command menu only once the command has been
                // accepted, so that cancelling the confirmation returns to the
                // menu.
                _delegate.sendCommandAndLeaveView( command );
            } else {
                throw new NonFatalUserInterfaceException( NonFatalUserInterfaceException.EX_INVALID_COMMAND );
            }
        } catch( ex ) {
            ExceptionHandler.handleUserInterfaceException( ex );
        }
    }

    // On back, return to the parent view without sending a command
    public function onBack() as Void {
        try {
            ViewStack.popView( WatchUi.SLIDE_RIGHT );
        } catch( ex ) {
            ExceptionHandler.handleUserInterfaceException( ex );
        }
    }
}