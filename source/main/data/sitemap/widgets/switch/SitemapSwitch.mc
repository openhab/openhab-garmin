import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Class representing `Switch` elements.
 *
 * `SitemapSwitch` has a corresponding `SwitchItem` that provides item-specific
 * properties required only by this widget.
 *
 * The command mappings defined in the sitemap will be honored, as will the
 * command description from the associated item.
 *
 * Since the widget should reflect state changes immediately after a command—
 * even before the next sitemap update—it supports updates via the `updateState()` function.
 * 
 * For this reason, it includes special handling for the `displayState` member
 * inherited from the base class. `displayState` is overwritten using the
 * description from the sitemap element mapping. During updates, this transformation
 * is re-applied, including the command and state descriptions from the item.
 * See `updateDisplayState()` for more details.
 */
class SitemapSwitch extends SitemapWidget {

    // The command descriptions to be applied
    // This is either the mappings from the sitemap widget, or
    // the command descriptions from the openHAB item 
    private var _commandDescriptions as CommandDescriptions;

    // Cached toggle mapping checks. The command descriptions are immutable
    // after construction, so these only need to be computed once.
    private var _hasToggleSwitchMappings as Boolean = false;
    private var _hasToggleSwitchMappingsWithoutDescriptions as Boolean = false;
    
    // The openHAB item associated with this widget
    private var _switchItem as SwitchItem;

    // The mappings from the sitemap element (widget)
    private var _mappings as CommandDescriptions;

    // The switch-specific display state
    private var _switchDisplayState as String;

    // Constructor
    public function initialize( 
        json as JsonAdapter, 
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) {
        // Obtain the item part of the element
        try {
            _switchItem = new SwitchItem( 
                json.getObject( "item", "Item not found. Check if the name is correct." ),
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
            _switchItem,
            null,
            isSitemapFresh,
            taskQueue
        );

        // Read the mappings. For Switch items, normalize them to exactly
        // ON/OFF; unsupported or incomplete mappings are discarded.
        var jsonMappings = json.getOptionalArray( "mappings" );
        if( _switchItem.getType().equals( "Switch" ) ) {
            jsonMappings = SwitchItem.normalizeSwitchCommandOptions( jsonMappings );
        }
        _mappings = new CommandDescriptions( jsonMappings );

        // ... and decide which command descriptions are to be used
        // If there are no widget mappings but there are
        // command descriptions, then we take those, otherwise
        // we take the widget mappings, even if empty
        if( ( ! _mappings.hasDescriptions() ) && _switchItem.getCommandDescriptions() != null ) {
            _commandDescriptions = _switchItem.getCommandDescriptions() as CommandDescriptions; 
        } else {
            _commandDescriptions = _mappings;
        }

        // The command descriptions are immutable after construction, so cache
        // the toggle checks here instead of recomputing them during selection.
        initializeToggleSwitchMappings();

        // For switch items, the mappings take precedence over the
        // display state provided in the sitemap element
        // Therefore we trigger a manual transformation here
        _switchDisplayState = generateSwitchDisplayState();
    }

    // Returns the command descriptions to be used by the widget (for non-toggle switches).
    // Priority is given to mappings defined in the sitemap. If none are present,
    // and the item provides command descriptions, those will be used instead.
    public function getCommandDescriptions() as CommandDescriptions {
        return _commandDescriptions;
    }

    // The associated item and its state
    public function getSwitchItem() as SwitchItem {
        return _switchItem;
    }

    // If there is a state, there will always be a display state
    public function hasDisplayState() as Boolean {
        return _switchItem.hasState();
    }

    // Returns true if mappings are defined (either via the mappings
    // in the sitemap or via command descriptions for the item)
    public function hasMappings() as Boolean {
        return _commandDescriptions.hasDescriptions();
    }

    // Returns true if the available command descriptions represent an ON/OFF
    // toggle switch. Explicit sitemap mappings take precedence over the item's
    // command descriptions, see the constructor.
    public function hasToggleSwitchMappings() as Boolean {
        return _hasToggleSwitchMappings;
    }

    // Returns true if the available command descriptions represent an ON/OFF
    // toggle switch and none of the commands has a description/label.
    public function hasToggleSwitchMappingsWithoutDescriptions() as Boolean {
        return _hasToggleSwitchMappingsWithoutDescriptions;
    }

    // Returns true if the available command descriptions represent an ON/OFF
    // toggle switch and at least one command has a description/label.
    public function hasDescribedToggleSwitchMappings() as Boolean {
        return _hasToggleSwitchMappings 
            && ! _hasToggleSwitchMappingsWithoutDescriptions;
    }

    // Determines and caches the toggle mapping checks. The command
    // descriptions are immutable after construction.
    private function initializeToggleSwitchMappings() as Void {
        _hasToggleSwitchMappings = false;
        _hasToggleSwitchMappingsWithoutDescriptions = false;

        if( _commandDescriptions.size() != 2 ) {
            return;
        }

        var hasOn = false;
        var hasOff = false;
        var hasDescriptions = false;
        for( var i = 0; i < _commandDescriptions.size(); i++ ) {
            var description = _commandDescriptions.getCommandDescription( i );
            if( ! description.getLabel().equals( "" ) ) {
                hasDescriptions = true;
            }

            var command = description.getCommand();
            if( command.equals( SwitchItem.ITEM_STATE_ON ) ) {
                hasOn = true;
            } else if( command.equals( SwitchItem.ITEM_STATE_OFF ) ) {
                hasOff = true;
            } else {
                return;
            }
        }

        if( hasOn && hasOff ) {
            _hasToggleSwitchMappings = true;
            _hasToggleSwitchMappingsWithoutDescriptions = ! hasDescriptions;
        }
    }

    // Transforms the raw state into a descriptive form for display.
    //
    // The transformation uses these sources in the following priority:
    // 1. A matching entry in the sitemap element’s mappings.
    // 2. The display state from the widget, if present.
    // 3. A matching entry in the item’s state descriptions.
    // 4. A matching entry in the item’s command descriptions.
    // 5. The raw state itself. If the state is numeric and a unit is defined for the item, the unit is appended.
    private function generateSwitchDisplayState() as String {
        var switchDisplayState = null;

        // First we lookup if there is a mapping or state/command metadata
        // that fits the state.
        // For Dimmer we skip that and always use the numeric state.
        if( ! _switchItem.getType().equals( "Dimmer" ) ) {
            // First priority: lookup the mappings defined for the widget
            switchDisplayState = _mappings.lookup( _switchItem.getState() );

            if( switchDisplayState == null ) {
                if( hasRemoteDisplayState() ) {
                    // Second priority: 
                    // If we got the state from the server, then the remoteDisplayState
                    // may be filled and we'll just use it.
                    // For internal updates this is never the case, since we
                    // set the displayState to NO_STATE before calling this function
                    switchDisplayState = getRemoteDisplayState();
                } else {
                    // Third priority: lookup the state description
                    switchDisplayState = _switchItem.lookupStateDescription( _switchItem.getState() ); 
                }
            }

            // Fourth priority: we lookup the command descriptions
            if( switchDisplayState == null ) {
                switchDisplayState = _switchItem.lookupCommandDescription( _switchItem.getState() );
            }
        }

        // If all has failed (or the item is a Dimmer), 
        // we just use the raw state, and if it is numeric add the unit
        if( switchDisplayState == null ) {
            if( _switchItem.hasState() ) {
                switchDisplayState = _switchItem.getState();
                // If the display state is numeric, then we
                // format it and add the unit
                var numericState = switchDisplayState.toFloat();
                if( numericState != null ) {
                    // Normal Float to String conversion would include
                    // the maximum number of decimal places, even if they
                    // are zero. Therefore we determine the actual number
                    // of decimal places and format to that precision.
                    switchDisplayState = SitemapNumeric.formatStateWithUnitShortCustom( numericState, _switchItem.getUnit() );
                }
            } else {
                switchDisplayState = NO_DISPLAY_STATE;
            }
        }

        return switchDisplayState;
    }

    // Display state for switch items is build from the various descriptions
    public function getDisplayState() as String { return _switchDisplayState; }

    // To be used to update the state if a change
    // is triggered from within the app
    public function updateState( state as Item.ItemState ) as Void {
        if( ! ( state instanceof String ) ) {
            throw new GeneralException( "SitemapSwitch.updateState supports only Strings." );
        }
        // If the state in the sitemap is the same as we got passed
        // in there is no need to update. updateState is relatively
        // costly due to the lookup of the description
        if( ! _switchItem.getState().equals( state ) ) {
            // This also updates the item, so we do not need to
            // do it here
            SitemapWidget.updateState( state );
            // generateSwitchDisplayState() needs both the 
            // item and the base class to process the update first
            _switchDisplayState = generateSwitchDisplayState();
        }
    }
}