import Toybox.Lang;
import Toybox.Timer;
import Toybox.WatchUi;

/*
 * Present a `Toybox.WatchUi.Confirmation` dialog before a command is sent.
 *
 * Sitemap widgets may declare a `commandConfirmMessage` (see `SitemapWidget`).
 * If such a message is present, the command must not be sent immediately.
 * `CommandMenuItem.sendCommand()` therefore calls this class instead of sending
 * the command directly whenever the message is non-empty. A confirmation
 * dialog showing exactly that message is presented to the user:
 *
 * - If the user confirms, the preserved command is sent exactly once.
 * - If the user cancels, no command is sent.
 *
 * The message is resolved by the openHAB server, so the client only displays
 * the provided text and never evaluates rules or conditions itself.
 *
 * ## View stack handling
 *
 * `CommandConfirmation` is invoked from `CommandMenuItem.sendCommand()`. Some
 * command callers (pickers, command-selection menus) dismiss their own view
 * right after requesting the command, e.g. via `ViewStack.popView()`. Pushing
 * the confirmation synchronously inside `sendCommand()` is therefore unsafe:
 * the caller's subsequent pop would remove the confirmation itself instead of
 * the originating view.
 *
 * To handle both kinds of callers (those that stay in their view and those
 * that dismiss it), the actual push is deferred until the current call stack
 * has unwound, using a one-shot `Timer.Timer`:
 *
 *     [originating view] sendCommand() schedules the confirmation push (timer)
 *     caller returns; caller pops its own view below          [view stays / popped]
 *     timer fires: push [confirmation] onto the (resulting) view stack
 *
 * This guarantees that the confirmation is always pushed on top of the final
 * view stack, regardless of whether the caller dismissed its own view.
 */
class CommandConfirmation {

    // The CommandMenuItem that will dispatch the command on confirmation.
    private var _delegate as CommandMenuItem;

    // The command value to send once the user confirms.
    // The type matches `Item.ItemState`.
    private var _command as Item.ItemState;

    // The confirmation message to display.
    private var _message as String;

    // True if the view underneath the confirmation shall be popped once the
    // command has been accepted.
    private var _leaveViewOnConfirm as Boolean;

    // The timer used to defer view stack changes until the current call stack
    // has unwound: the presentation push, or leaving the calling view after the
    // user confirmed.
    private var _timer as Timer.Timer?;

    // Prevent multiple dispatch in case onResponse is invoked more than once.
    private var _handled as Boolean = false;

    // A confirmation whose presentation is scheduled but not yet shown. At most
    // one deferred confirmation may be pending. If a new command is requested
    // before the timer fires, the previous pending presentation is replaced so
    // that multiple confirmations never stack on the view stack.
    private static var _pendingPresentation as CommandConfirmation?;

    /*
     * Presents a confirmation dialog for the given command.
     *
     * Callers must only invoke this when the widget declares a non-empty
     * confirmation message; commands without a message are dispatched directly
     * by `CommandMenuItem.sendCommand()`.
     *
     * @param delegate - The `CommandMenuItem` that will dispatch the command.
     * @param command - The command to send (String or Float).
     * @param message - The confirmation message to display; must not be empty.
     * @param leaveViewOnConfirm - If true, the calling view is popped once the
     *                             user confirms; the caller must not pop it
     *                             itself in that case.
     */
    public static function show(
        delegate as CommandMenuItem,
        command as Item.ItemState,
        message as String,
        leaveViewOnConfirm as Boolean
    ) as Void {
        if( message.equals( "" ) ) {
            throw new GeneralException( "CommandConfirmation requires a non-empty confirmation message." );
        }

        var confirmation = new CommandConfirmation( delegate, command, message, leaveViewOnConfirm );
        confirmation.presentDeferred();
    }

    // Constructor
    private function initialize(
        delegate as CommandMenuItem,
        command as Item.ItemState,
        message as String,
        leaveViewOnConfirm as Boolean
    ) {
        _delegate = delegate;
        _command = command;
        _message = message;
        _leaveViewOnConfirm = leaveViewOnConfirm;
    }

    // Defers the presentation until after the current call stack has unwound,
    // so that a caller popping its own view right after requesting the command
    // does so before the confirmation is pushed.
    //
    // Public because it is invoked from the static `show()` factory. Monkey C
    // cannot resolve `protected` methods through an object reference from a
    // static function and fails at runtime with a "Symbol Not Found" error.
    public function presentDeferred() as Void {
        // If a presentation is already scheduled but not yet shown, replace it
        // so that confirmations never stack on the view stack.
        if( _pendingPresentation != null ) {
            _pendingPresentation.cancel();
        }
        _pendingPresentation = self;

        // Setting the timer to 50ms defers execution to a later event loop
        // iteration, after the originating call stack has unwound.
        _timer = new Timer.Timer();
        _timer.start( method( :present ), 50, false );
    }

    // Prevents this (not yet presented) confirmation from being shown. Used to
    // replace a stale deferred presentation when a newer command is requested.
    // Public because it is invoked through an object reference.
    public function cancel() as Void {
        if( _timer != null ) {
            _timer.stop();
            _timer = null;
        }
        if( _pendingPresentation == self ) {
            _pendingPresentation = null;
        }
    }

    // Pushes the confirmation dialog onto the view stack.
    // Public because it is referenced as `method(:present)` for the timer, and
    // method references only resolve public symbols.
    public function present() as Void {
        _timer = null;
        if( _pendingPresentation == self ) {
            _pendingPresentation = null;
        }
        ViewStack.pushView(
            new WatchUi.Confirmation( _message ),
            new CommandConfirmationDelegate( self ),
            WatchUi.SLIDE_IMMEDIATE
        );
    }

    // Called by the delegate when the user confirms or cancels.
    // @param response - WatchUi.CONFIRM_YES or WatchUi.CONFIRM_NO
    public function onResponse( response as WatchUi.Confirm ) as Void {
        if( _handled ) {
            // Safety net against duplicate callbacks
            return;
        }
        _handled = true;

        // The system dismisses the Confirmation by popping the top view after
        // this callback returns. Only the tracked stack is updated here; view
        // stack changes are deferred until the dialog is gone.
        ViewStack.discardTopView();

        if( response == WatchUi.CONFIRM_YES ) {
            // Dispatch the preserved command exactly once, without re-entering
            // the confirmation flow.
            _delegate.dispatchCommand( _command );

            if( _leaveViewOnConfirm ) {
                leaveViewDeferred();
            }
        }
        // CONFIRM_NO: nothing is sent and the calling view stays.
    }

    // Defers leaving the calling view until the system has dismissed the
    // dialog. Popping directly in onResponse() would make the system pop the
    // next view instead.
    private function leaveViewDeferred() as Void {
        _timer = new Timer.Timer();
        _timer.start( method( :leaveView ), 50, false );
    }

    // Pops the calling view once the dialog is gone.
    // Public because it is referenced via method(:leaveView).
    public function leaveView() as Void {
        _timer = null;
        ViewStack.popView( WatchUi.SLIDE_RIGHT );
    }
}