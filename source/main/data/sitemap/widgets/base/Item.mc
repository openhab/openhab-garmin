import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

/*
 * Represents an openHAB item and its current state.
 *
 * For efficiency, this class includes only the item properties common to all types.
 * Subclasses, such as `SwitchItem`, add properties specific to their respective types.
 */
class Item {

    // Data types accepted for item state updates.
    // This class's updateState() method supports only String arguments values.
    // Subclasses may override it to handle additional data types.
    typedef ItemState as String or Float;
    
    // If state is missing, NULL or UNDEF it will be set to this constant.
    public static const NO_STATE = "NULL";

    // The unit symbol for percentages. Percentages are always displayed
    // without decimal places, see `SitemapNumeric.formatStateWithUnitShortCustom()`.
    public static const UNIT_PERCENT = "%";

    // See the get accessors for documentation
    private var _name as String;
    private var _type as String;
    private var _state as String;
    private var _unit as String;

    //! Constructor
    //! @param json The JSON object from which this Item should be built.
    public function initialize( json as JsonAdapter, isSitemapFresh as Boolean ) {
        _name =  json.getString(
            "name",
            Application.loadResource( Rez.Strings.ErrorItemNameMissing ) as String
        );

        // If the item is a Group, we apply the groupType
        _type =  json.getString(
            "type",
            Lang.format(
                Application.loadResource( Rez.Strings.ErrorItemWithoutType ) as String,
                [ _name ]
            )
        );
        if( _type.equals( "Group" ) ) {
            var groupType = json.getOptionalString( "groupType" );
            if( ! groupType.equals( "" ) ) {
                _type = groupType;
            }            
        }

        // If no state is available, it will be set to null
        _state = json.getOptionalString( "state" );
        if( ! isSitemapFresh
            || _state == null 
            || _state.equals( "" ) 
            || _state.equals( "NULL" )
            || _state.equals( "UNDEF" )
        ) {
            _state = NO_STATE;
        }

        // For dimmer and rollershutter we always use "%",
        // regardless of the JSON. For all other types we
        // use the unit from the JSON if present
        if( _type.equals( "Dimmer" ) ) {
            _unit = UNIT_PERCENT;
        } else if( _type.equals( "Rollershutter" ) ) {
            _unit = UNIT_PERCENT;
        } else {
            _unit = json.getOptionalString( "unitSymbol" );
        }
    }

    //! Name of the item.
    public function getName() as String { return _name; }

    //! Type of the item. 
    //! If the item type is Group, then this value is set to the groupType.
    public function getType() as String { return _type; }

    //! State of the item.
    //! If the state is missing, NULL or UNDEF it is set to `NO_STATE`, which is displayed as an em dash by the widgets.
    public function getState() as String { return _state; }

    //! Unit of the item.
    //! The unit is set to "%" for Dimmer and Rollershutter items, and otherwise filled from the unit provided with the JSON, if available. If neither applies it is an empty string ("").
    public function getUnit() as String { return _unit; }

    //! Determine whether the item has a valid state
    //! @return true if the state is not `NO_STATE`
    public function hasState() as Boolean {
        return ! _state.equals( NO_STATE );
    }

    //! Updates the item state
    //! This class's updateState() method supports only String arguments values.
    //! Subclasses may override it to handle additional data types.
    public function updateState( state as ItemState ) as Void {
        if( ! ( state instanceof String ) ) {
            throw new GeneralException( "Item.updateState supports only Strings." );
        }
        _state = state;
    }
}