import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

/*
 * Class representing the item associated with a `SitemapNumeric`.
 *
 * The Numeric widget uses data from the base item, and this class
 * adds the state display into a numeric value. This ensures
 * that the widget implementation can rely on the state being numeric.
 */
class NumericItem extends Item {

    private var _numericState as Float = 0.0;

    // Constructor
    public function initialize( json as JsonAdapter, isSitemapFresh as Boolean ) {
        Item.initialize( json, isSitemapFresh );

        // If there is no state, we leave the numeric
        // state at 0, analogue to how the 
        // openHAB Main UI handles it
        if( hasState() ) {
            var numericState = getState().toFloat();
            if( numericState != null ) {
                _numericState = numericState;
            } else {
                throw new JsonParsingException( Application.loadResource( Rez.Strings.ErrorStateNotNumeric ) as String );
            }
        }
    }

    // Returns the numeric state
    public function getNumericState() as Float { return _numericState; }

    // Rounds the state to a given number of decimal places
    public function roundState( decimalPlaces as Number ) as Void { 
        // If there is no state, leave it unchanged.
        // When no state is present, the numeric value is still zero.
        // Calling updateNumericState would therefore overwrite the
        // "no state" string with "0", which must be avoided.
        if( hasState() ) {
            updateState( CustomMath.round( _numericState, decimalPlaces ).toFloat() );
        }
    }

    // Updates the numeric state as well as the
    // string state of the base class
    public function updateState( numericState as Item.ItemState ) as Void {
        if( ! ( numericState instanceof Float ) ) {
            throw new GeneralException( "NumericItem.updateState supports only Float arguments." );
        }
        _numericState = numericState;
        Item.updateState( numericState.toString() );
    }
}