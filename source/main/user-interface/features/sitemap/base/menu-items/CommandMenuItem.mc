import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;
import Toybox.Time;

class CommandMenuItem extends WidgetMenuItem {

    private var _commandRequest as BaseCommandRequest?;

    // The last time an internal update was applied. Internal updates are applied
    // by some menu items directly after a command is sent to immediately reflect
    // the new state. Storing this timestamp is required to apply the post-command
    // hold time configured in the app settings.
    private var _lastLocalStateUpdate as Moment?;

    private var _pendingCommand as Item.ItemState?;

    // Constructor
    protected function initialize( 
        options as BaseWidgetMenuItemOptions
    ) {
        WidgetMenuItem.initialize( options );
        _commandRequest = BaseCommandRequest.get( self, false );
    }

    private function getItem() as Item {
        var item = getSitemapWidget().getItem();
        if( item == null ) {
            throw new GeneralException( "CommandMenuItem does not support widgets without item." );
        }
        return item;
    }

    // This function is called during a command request to identify
    // the target item that the command should be sent to.
    public function getItemName() as String {
        return getItem().getName();
    }

    protected function getPendingCommand() as String? {
        return _pendingCommand != null
               ? _pendingCommand.toString()
               : null;
    }

    protected function hasCommandsEnabled() as Boolean {
        return _commandRequest != null;
    }

    protected function hasPendingCommand() as Boolean {
        return _pendingCommand != null;
    }

    // Returns true if the item is still within the configured post-command hold time
    // since the last internal state update.
    public function isInHoldTime() as Boolean {
        var postCommandHoldTime = AppSettings.getPostCommandHoldTime();
        if( _lastLocalStateUpdate != null && postCommandHoldTime.value() > 0 ) {
            return Time.now().lessThan( _lastLocalStateUpdate.add( postCommandHoldTime ) );
        } else {
            return false;
        }
    }

    public function onCommandComplete() as Void {
        if( _pendingCommand != null ) {
            // Perform an internal update of the state in the SitemapSwitch
            getSitemapWidget().updateState( _pendingCommand as Item.ItemState );
            _pendingCommand = null;
            
            // This is used to apply the post-command hold time for state updates,
            // if configured in the app settings.
            _lastLocalStateUpdate = Time.now();

            // Update state displayed by the menu item
            onStateChangedLocally();
 
            WatchUi.requestUpdate();
       }
    }

    // C
    public function onCommandDeferredToSync() as Void {
        _pendingCommand = null;
    }

    // Exceptions from the command request are handed
    // over to the ExceptionHandler
    public function onCommandException( ex as Exception ) as Void {
        _pendingCommand = null;
    }

    public function onStateChangedLocally() as Void {
        setIcon( getSitemapWidget().getIcon() );
        onStateChanged();
    }

    public function onStateChanged() as Void {
    }

    // Sends the command via the CommandRequest.
    // This method allows a command to be sent even if another command
    // is currently pending. In that case, the pending command is replaced
    // and the underlying request cancels the previous one.
    // Subclasses that want to prevent sending a new command while one
    // is pending should call hasPendingCommand() before invoking
    // sendCommand().
    //
    // If the widget declares a non-empty `commandConfirmMessage`, a
    // confirmation dialog is shown first and the command is only sent after
    // the user confirms it. Otherwise the command is dispatched immediately.
    public function sendCommand( command as Item.ItemState ) as Void {
        if( _commandRequest != null ) {
            CommandConfirmation.show(
                self,
                command,
                getSitemapWidget().getCommandConfirmMessage()
            );
        }
    }

    // Dispatches a command via the CommandRequest without any confirmation.
    // This is the low-level command submission used both for the immediate
    // path (no confirmation message) and by CommandConfirmation after the
    // user confirms, so a confirmed command is never confirmed again.
    public function dispatchCommand( command as Item.ItemState ) as Void {
        if( _commandRequest != null ) {
            _pendingCommand = command;
            _commandRequest.sendCommand( command );
        }
    }

    // Called by the sitemap request when updated state data is received.
    public function updateWidget( sitemapWidget as SitemapWidget ) as Void {
        // Before updating the base class, we keep the previous item
        // to check if the item name has changed.
        var previousItem = getItem();

        WidgetMenuItem.updateWidget( sitemapWidget );

        // If the item has changed, we create a new command request
        if( ! previousItem.getName().equals( getItem().getName() ) ) {
            _commandRequest = BaseCommandRequest.get( self, false );
        }
    }
}