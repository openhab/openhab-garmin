import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;
import Toybox.System;

class CommandMenuItem extends WidgetMenuItem {

    private var _commandRequest as BaseCommandRequest?;

    // The value of System.getTimer() (in ms) when the last internal update was
    // applied. Internal updates are applied by some menu items directly after a
    // command is sent to immediately reflect the new state. Storing this
    // timestamp is required to apply the post-command hold time configured in
    // the app settings.
    private var _lastLocalStateUpdateTimer as Number?;

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
        var holdMillis = AppSettings.getPostCommandHoldTimeMillis();
        if( _lastLocalStateUpdateTimer != null && holdMillis > 0 ) {
            var elapsed = System.getTimer() - ( _lastLocalStateUpdateTimer as Number );
            // A negative elapsed time can only occur if the millisecond timer
            // wrapped around (about every 50 days). The hold has then certainly
            // elapsed.
            return elapsed >= 0 && elapsed < holdMillis;
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
            _lastLocalStateUpdateTimer = System.getTimer();

            // Update state displayed by the menu item
            onStateChangedLocally();

            // The item now shows a locally applied state. Until the configured
            // post-command hold time has elapsed, an unchanged response cannot
            // reconcile it, so upcoming responses must not be skipped.
            SitemapStore.noteModelDivergenceFor( AppSettings.getPostCommandHoldTimeMillis() );
 
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
        if( _commandRequest == null ) {
            return;
        }

        var confirmMessage = getSitemapWidget().getCommandConfirmMessage();
        if( confirmMessage.equals( "" ) ) {
            dispatchCommand( command );
        } else {
            CommandConfirmation.show( self, command, confirmMessage, false );
        }
    }

    // Sends the command like sendCommand(), but leaves the calling view once
    // the command has been accepted: the view is popped immediately if no
    // confirmation is configured, or after the user confirmed the dialog. When
    // the user cancels, nothing is sent and the view stays open.
    //
    // The caller must not pop the view itself. Not to be used where the view is
    // dismissed by the system (e.g. ActionMenu).
    public function sendCommandAndLeaveView( command as Item.ItemState ) as Void {
        if( _commandRequest == null ) {
            // There is nothing to send or confirm, but still honor the request
            // to leave the view.
            ViewStack.popView( WatchUi.SLIDE_RIGHT );
            return;
        }

        var confirmMessage = getSitemapWidget().getCommandConfirmMessage();
        if( confirmMessage.equals( "" ) ) {
            dispatchCommand( command );
            ViewStack.popView( WatchUi.SLIDE_RIGHT );
        } else {
            CommandConfirmation.show( self, command, confirmMessage, true );
        }
    }

    // Sends the command without presenting a confirmation dialog, even if the
    // widget declares a `commandConfirmMessage`.
    //
    // Callers use this for commands that are not discrete user actions, e.g.
    // intermediate values while a slider is moved or a state that is reverted
    // on cancel, for which a confirmation would be disruptive.
    public function sendCommandWithoutConfirmation( command as Item.ItemState ) as Void {
        dispatchCommand( command );
    }

    // Dispatches a command via the CommandRequest without any confirmation.
    // This is the low-level command submission used by
    // sendCommandWithoutConfirmation() and by CommandConfirmation after the
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