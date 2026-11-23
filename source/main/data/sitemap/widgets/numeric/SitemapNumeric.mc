import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

/*
 * Class representing `Setpoint` and `Slider` elements.
 */

class SitemapNumeric extends SitemapWidget {

    // Maximum number of decimal places displayed for
    // Setpoint and Slider widgets, as well as for the
    // numeric value shown in Dimmer toggle items.
    public static const UI_NUMERIC_MAX_DECIMAL_POINTS as Number = 4;
    
    // Maximum number of steps ((maxValue - minValue) / step)
    // supported by Setpoint and Slider widgets.
    public static const UI_NUMERIC_MAX_STEPS as Number = 100;

    private var _numericDisplayState as String;

    private var _numericItem as NumericItem;

    // Setpoint/Slider properties
    private var _minValue as Float;
    private var _maxValue as Float;
    private var _step as Float;
    private var _releaseOnly as Boolean; // Slider only
 
    // The number of decimal places to be displayed
    private var _decimalPlaces as Number;

    // Constructor
    public function initialize( 
        json as JsonAdapter, 
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) {
        // Obtain the item part of the element
        try {
            _numericItem = new NumericItem( 
                json.getObject(
                    "item",
                    Application.loadResource( Rez.Strings.ErrorItemNotFound ) as String
                ),
                isSitemapFresh 
            );
        } catch( ex ) {
            // The item does not have the type/label, so we add it to any
            // exception thrown when creating the item. To be able to
            // access the type/label, we need to initialize the base class
            SitemapWidget.initialize( json, null, null, isSitemapFresh, taskQueue );
            throw new JsonParsingException( 
                getType() + " '" + getLabel() + "': " + ex.getErrorMessage() );
        }

        // The base class relies on the item for parsing the icon, 
        // therefore we initialize it after the item was created
        SitemapWidget.initialize( 
            json, 
            _numericItem, 
            null, 
            isSitemapFresh,
            taskQueue
        );

        var hasState = _numericItem.hasState();

        _minValue = json.getFloat( "minValue", 0.0 );
        _maxValue = json.getFloat( "maxValue", 100.0 );
        _step = json.getFloat( "step", 1.0 );

        // Reject invalid parameters before they are used in any calculation.
        // A step that is not positive makes the picker loop move away from
        // maxValue instead of towards it, and an inverted range would leave
        // the picker without any pickable value.
        if( _step <= 0.0 ) {
            throw new JsonParsingException( 
                getType() + " '" + getLabel() + "': "
                + ( Application.loadResource( Rez.Strings.ErrorNumericStepInvalid ) as String ) );
        }

        if( _maxValue < _minValue ) {
            throw new JsonParsingException( 
                getType() + " '" + getLabel() + "': "
                + ( Application.loadResource( Rez.Strings.ErrorNumericMinMaxInvalid ) as String ) );
        }

        // Verify that we do not have more steps than supported
        if( UI_NUMERIC_MAX_STEPS < ( _maxValue - _minValue ) / _step ) {
            throw new JsonParsingException( Lang.format(
                Application.loadResource( Rez.Strings.ErrorNumericTooManySteps ) as String,
                [ getType() ]
            ) );
        }

        // For display purposes, only as many decimal places
        // as defined by the step value are shown.
        _decimalPlaces = CustomMath.getDecimalPlaces( _step, UI_NUMERIC_MAX_DECIMAL_POINTS );
        _numericItem.roundState( _decimalPlaces );

        // If there is no state, the releaseOnly mode makes more
        // sense, since without release only, a known state is required
        // for cancelling, and also in WLAN mode, it does not make
        // sense to send a command for every step in the number picker
        // Therefore without state, we enable it even if the parameter was not set
        _releaseOnly = 
            hasState
            ? json.getBoolean( "releaseOnly" )
            : true;

        // For numeric sitemap widgets, we override the display state to
        // achieve formatting consistent with the full-screen widgets,
        // while conserving space by omitting the space between value
        // and unit and showing only the decimal places present in the value.
        _numericDisplayState = 
            hasState
            ? formatStateWithUnitShort( _numericItem.getNumericState() )
            : NO_DISPLAY_STATE;
    }

    // The following functions convert a numeric state into a
    // formatted string representation for display.
    //
    // They accept the state as a parameter so they can also be used
    // externally to format arbitrary states, not only the current one.

    // Returns the numeric state formatted as a display string,
    // using the number of decimal places defined by the
    // step value of this widget.
    public function formatState( state as Float ) as String {
        return formatStateWithDecimals( state, _decimalPlaces );
    }

    // Returns the numeric state formatted as a display string,
    // using the number of decimal places provided as a parameter.
    public static function formatStateWithDecimals( state as Float, decimalPlaces as Number ) as String {
        return state.format( "%." + decimalPlaces + "f" );
    }

    // Returns the numeric state formatted as a display string,
    // appending the unit without a separating space and using
    // only the decimal places present in the state value.
    //
    // For example, if the state is 20, it is formatted as 20.
    // If the state is 20.5, it is formatted as 20.5. 
    //
    // Intended for menu item display, where conserving space
    // is important.
    public function formatStateWithUnitShort( state as Float ) as String {
        return formatStateWithUnitShortCustom( state, _numericItem.getUnit() );
    }
    
    // Same as formatStateWithUnitShort, but uses the unit
    // provided as a parameter instead of the internally stored one.
    //
    // Can be used by other classes to ensure formatting consistent
    // with the rules applied by this class.
    public static function formatStateWithUnitShortCustom( state as Float, unit as String ) as String {
        return formatStateWithDecimals( 
            state, 
            CustomMath.getDecimalPlaces( state, UI_NUMERIC_MAX_DECIMAL_POINTS ) 
        ) + unit;
    }

    // Returns the numeric state formatted as a display string,
    // appending the unit with a separating space (except for "%")
    // and using the full number of decimal places defined by the
    // step value of this Numeric widget.
    //
    // For example, if step is 0.5, the value 20 is formatted as 20.0.
    //
    // Used for display in the full-screen NumberPicker view.
    public function formatStateWithUnitLong( state as Float ) as String {
        var unit = _numericItem.getUnit();
        if( unit.length() > 0 && ! unit.equals( "%" ) ) {
            unit = " " + unit;
        }
        return formatState( state ) + unit;
    }

    // Display state for numeric items is build from
    // the item state and item unit
    public function getDisplayState() as String { return _numericDisplayState; }

    // Returns the numeric item subclass
    public function getNumericItem() as NumericItem { return _numericItem; }

    // Setpoint/Slider properties
    public function getMinValue() as Float { return _minValue; }
    public function getMaxValue() as Float { return _maxValue; }
    public function getStep() as Float { return _step; }

    // If there is a state, there will always be a display state
    public function hasDisplayState() as Boolean {
        return _numericItem.hasState();
    }

    // Slider property
    public function isReleaseOnly() as Boolean { return _releaseOnly; }

    // To be used to update the state if a change
    // is triggered from within the app
    public function updateState( numericState as Item.ItemState ) as Void {
        if( ! ( numericState instanceof Float ) ) {
            throw new GeneralException( "SitemapNumeric.updateState supports only Float arguments." );
        }
        // If the state in the sitemap is the same as we got passed
        // in there is no need to update.
        if( _numericItem.getNumericState() != numericState ) {
            // This also updates the item, so we do not need to
            // do it here
            SitemapWidget.updateState( numericState );
            // We use the short format, see constructor for details
            _numericDisplayState = formatStateWithUnitShort( numericState );
            // Without a state, we always operate in release only
            // mode. See constructor for details.
            if( ! _numericItem.hasState() ) {
                _releaseOnly = true;
            }
        }
    }
}