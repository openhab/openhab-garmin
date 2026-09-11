import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Input delegate for `WatchUi.Confirmation` dialogs shown by `CommandConfirmation`.
 *
 * It simply forwards the user's response to the owning `CommandConfirmation`,
 * which is responsible for popping the dialog and dispatching the command.
 *
 * This class exists so that a `WatchUi.Confirmation` can be presented on the
 * view stack with the correct delegate type, keeping the command handling in
 * `CommandConfirmation`.
 */
class CommandConfirmationDelegate extends WatchUi.ConfirmationDelegate {

    // The CommandConfirmation that owns this delegate
    private var _confirmation as CommandConfirmation;

    // Constructor
    public function initialize( confirmation as CommandConfirmation ) {
        ConfirmationDelegate.initialize();
        _confirmation = confirmation;
    }

    // Called by the system once the user confirms or cancels.
    // @param response - WatchUi.CONFIRM_YES or WatchUi.CONFIRM_NO
    // @return true if the response was handled.
    public function onResponse( response as WatchUi.Confirm ) as Boolean {
        _confirmation.onResponse( response );
        return true;
    }
}
