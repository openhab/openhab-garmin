import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Presents the widget's `commandConfirmMessage` in front of the numeric picker,
 * which has already been opened for a non-releaseOnly value.
 *
 * In that mode every change is sent to the server immediately, so confirming
 * each step would be impractical. Instead, the user confirms the interaction
 * once, before the picker becomes visible. Because the picker is already open
 * underneath the dialog, confirming only dismisses the dialog and reveals the
 * picker, with no intermediate view being shown.
 */
class NumericPickerOpenConfirmation {

    // Presents the confirmation dialog on top of the already opened picker.
    public static function show( message as String ) as Void {
        ViewStack.pushView(
            new WatchUi.Confirmation( message ),
            new NumericPickerOpenConfirmationDelegate(),
            WatchUi.SLIDE_IMMEDIATE
        );
    }
}

/*
 * Input delegate for the confirmation dialog shown in front of the numeric
 * picker.
 */
class NumericPickerOpenConfirmationDelegate extends WatchUi.ConfirmationDelegate {

    // Prevent duplicate responses from modifying the view stack twice.
    private var _handled as Boolean = false;

    // Constructor
    public function initialize() {
        ConfirmationDelegate.initialize();
    }

    // Called by the system once the user confirms or cancels.
    // @param response - WatchUi.CONFIRM_YES or WatchUi.CONFIRM_NO
    // @return true if the response was handled.
    public function onResponse( response as WatchUi.Confirm ) as Boolean {
        if( _handled ) {
            return true;
        }
        _handled = true;

        // The system dismisses the Confirmation by popping the top view *after*
        // this callback returns. The tracked view stack is adjusted so that it
        // matches the resulting system stack.
        if( response == WatchUi.CONFIRM_YES ) {
            // Confirm: the picker underneath stays. Only the dialog entry is
            // removed; the system dismisses the dialog itself.
            ViewStack.discardTopView();
        } else {
            // Cancel: close the dialog, then drop the picker entry because the
            // system's dismissal pop then removes the picker, which is the top
            // view once the dialog is gone.
            ViewStack.popView( WatchUi.SLIDE_RIGHT );
            ViewStack.discardTopView();
        }

        return true;
    }
}
