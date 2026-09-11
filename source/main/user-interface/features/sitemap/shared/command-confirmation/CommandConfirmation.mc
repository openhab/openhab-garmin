import Toybox.Lang;
import Toybox.Timer;
import Toybox.WatchUi;

/*
 * Present a `Toybox.WatchUi.Confirmation` dialog before a command is sent.
 *
 * Sitemap widgets may declare a `commandConfirmMessage` (see `SitemapWidget`).
 * If such a message is present and non-empty, the command must not be sent
 * immediately. Instead, a confirmation dialog showing exactly that message is
 * presented to the user:
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

    // The timer used to defer the actual push until the current call stack has
    // unwound. This keeps the object alive until it is presented.
    private var _timer as Timer.Timer?;

    // Prevent multiple dispatch in case onResponse is invoked more than once.
    private var _handled as Boolean = false;

    // A confirmation whose presentation is scheduled but not yet shown. At most
    // one deferred confirmation may be pending. If a new command is requested
    // before the timer fires, the previous pending presentation is replaced so
    // that multiple confirmations never stack on the view stack.
    private static var _pendingPresentation as CommandConfirmation?;

    /*
     * Presents a confirmation dialog for the given command, or dispatches the
     * command immediately if no confirmation message is set.
     *
     * @param delegate - The `CommandMenuItem` that will dispatch the command.
     * @param command - The command to send (String or Float).
     * @param message - The confirmation message; an empty string means no
     *                  confirmation is required.
     */
    public static function show(
        delegate as CommandMenuItem,
        command as Item.ItemState,
        message as String
    ) as Void {
        // No message -> existing immediate behavior
        if( message.equals( "" ) ) {
            delegate.dispatchCommand( command );
            return;
        }

        var confirmation = new CommandConfirmation( delegate, command, message );
        confirmation.presentDeferred();
    }

    // Constructor
    private function initialize(
        delegate as CommandMenuItem,
        command as Item.ItemState,
        message as String
    ) {
        _delegate = delegate;
        _command = command;
        _message = message;
    }

    // Defers the presentation until after the current call stack has unwound,
    // so that a caller popping its own view right after requesting the command
    // does so before the confirmation is pushed.
    // Non-private so it can be called through an object reference from `show()`.
    protected function presentDeferred() as Void {
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
    // Non-private so it can be called through an object reference.
    protected function cancel() as Void {
        if( _timer != null ) {
            _timer.stop();
            _timer = null;
        }
        if( _pendingPresentation == self ) {
            _pendingPresentation = null;
        }
    }

    // Pushes the confirmation dialog onto the view stack.
    // Non-private so it can be referenced via `method(:present)` for the timer.
    protected function present() as Void {
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

        // Pop the confirmation dialog. Since it was pushed via ViewStack, we
        // pop it there as well to keep the app's view stack in sync.
        ViewStack.popView( WatchUi.SLIDE_IMMEDIATE );

        if( response == WatchUi.CONFIRM_YES ) {
            // Dispatch the preserved command exactly once, without re-entering
            // the confirmation flow.
            _delegate.dispatchCommand( _command );
        }
        // CONFIRM_NO: nothing is sent.
    }
}