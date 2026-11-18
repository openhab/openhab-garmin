import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Delegate for the Setpoint/Slider widget ("Numeric") that handles user input 
 * within the associated DynamicPicker.
 *
 * Behavior depends on the releaseOnly flag:
 *
 * - If releaseOnly is **false**:
 *   - Every up/down movement immediately sends a command to update the value.
 *   - Confirm leaves the last set value in place.
 *   - Cancel reverts to the value that was active before the DynamicPicker was opened.
 *
 * - If releaseOnly is **true**:
 *   - The value is only updated when the user confirms (via accept).
 *   - Cancel leaves the value unchanged from when the DynamicPicker was opened.
 */
class NumericPickerDelegate extends DynamicPickerDelegate {

    // Reference to the menu item is needed for sending
    // commands
    private var _menuItem as NumericMenuItem;

    // The state with which the DynamicPicker was entered,
    // in case we need to reset on cancellation
    private var _previousState as Float;

    // Constructor
    public function initialize( menuItem as NumericMenuItem ) {
        DynamicPickerDelegate.initialize();
        _menuItem = menuItem;
        _previousState = 
            menuItem.getSitemapNumeric().getNumericItem().getNumericState();
    }

    // If the user confirms and we ARE in releaseOnly mode,
    // then we change the state here. Otherwise we just
    // pop the Picker from the view stack.
    public function onAccept( newState as Object ) as Boolean {
        // Logger.debug "NumericPickerDelegate.onAccept" );
        if( _menuItem.getSitemapNumeric().isReleaseOnly() ) {
            if( ! ( newState instanceof Float ) ) {
                throw new NonFatalUserInterfaceException( NonFatalUserInterfaceException.EX_INVALID_STATE_TYPE );
            }
            // Logger.debug "NumericPickerDelegate.onAccept: new state=" + newState.toString() );
            // Leave the picker only once the command has been accepted, so that
            // cancelling the confirmation returns to the picker.
            _menuItem.sendCommandAndLeaveView( newState );
        } else {
            // No command is sent on accept in this mode, so leave right away.
            ViewStack.popView( WatchUi.SLIDE_RIGHT );
        }
        return true;
    }    

    // If the user cancels and we ARE NOT in releaseOnly mode,
    // then we revert the state here. Otherwise we just
    // pop the Picker from the view stack.
    public function onCancel() as Boolean {
        // Logger.debug "NumericPickerDelegate.onCancel" );
        if( ! _menuItem.getSitemapNumeric().isReleaseOnly() ) {
            if( _menuItem.getSitemapNumeric().getNumericItem().getNumericState() != _previousState ) {
                // Logger.debug "NumericPickerDelegate.onCancel: reverting to state=" + _previousState.toString() );
                // Reverting is not a deliberate command selection, so it must
                // not present a confirmation dialog.
                _menuItem.sendCommandWithoutConfirmation( _previousState );
            } else {
                // Logger.debug "NumericPickerDelegate.onCancel: state did not change" );
            }
        }
        ViewStack.popView( WatchUi.SLIDE_RIGHT );
        return true;
    }

    // up/down both use the same internal function
    // to update the state, which will do so only
    // if releaseOnly is false
    public function onUp( value as Object ) as Boolean {
        updateState( value );
        return true;
    }
    public function onDown( value as Object ) as Boolean {
        updateState( value );
        return true;
    }
    private function updateState( state as Object ) as Void {
        // Logger.debug "NumericPickerDelegate.updateState=" + state );
        if( ! ( state instanceof Float ) ) {
            throw new NonFatalUserInterfaceException( NonFatalUserInterfaceException.EX_INVALID_STATE_TYPE );
        }
        // Update only if releaseOnly is false
        if( ! _menuItem.getSitemapNumeric().isReleaseOnly() ) {
            // Intermediate values are sent while the user is still moving the
            // picker, so they must not present a confirmation dialog.
            _menuItem.sendCommandWithoutConfirmation( state );
        }
    }     
}