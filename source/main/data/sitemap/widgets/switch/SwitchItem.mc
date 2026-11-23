import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

/*
 * Class representing the item associated with a `SitemapSwitch`.
 *
 * Switch is the only item type that requires both command and state descriptions.
 * If present, these are used to determine the available commands and to display
 * the current state. See `SitemapSwitch.generateSwitchDisplayState()` for details.
 *
 * For item type `Switch`, command descriptions are normalized to exactly `ON`
 * and `OFF`. Unsupported or incomplete command sets are discarded.
 */
class SwitchItem extends Item {

    // Strings representing the on/off state
    public static const ITEM_STATE_ON = "ON";
    public static const ITEM_STATE_OFF = "OFF";

    // Strings representing the states of a rollershutter item
    public static const ITEM_STATE_UP = "UP";
    public static const ITEM_STATE_DOWN = "DOWN";
    public static const ITEM_STATE_STOP = "STOP";

    // Strings representing the open/closed states
    public static const ITEM_STATE_OPEN = "OPEN";
    public static const ITEM_STATE_CLOSED = "CLOSED";

    // Strings representing the play/pause states
    // and the next/previous commands
    public static const ITEM_STATE_PLAY = "PLAY";
    public static const ITEM_STATE_PAUSE = "PAUSE";
    public static const ITEM_COMMAND_NEXT = "NEXT";
    public static const ITEM_COMMAND_PREVIOUS = "PREVIOUS";

    private var _commandDescriptions as CommandDescriptions?;
    private var _stateDescriptions as StateDescriptions?;

    // Constructor
    public function initialize( json as JsonAdapter, isSitemapFresh as Boolean ) {
        Item.initialize( json, isSitemapFresh );

        // Then we read the item's command descriptions, if there are any ...
        var jsonCommandDescriptions = json.getOptionalObject( "commandDescription" );
        if( jsonCommandDescriptions != null ) {
            var commandOptions = jsonCommandDescriptions.getOptionalArray( "commandOptions" );
            if( getType().equals( "Switch" ) ) {
                commandOptions = normalizeSwitchCommandOptions( commandOptions );
            }
            _commandDescriptions = new CommandDescriptions( commandOptions );
        }

        // ... and do the same for the state descriptions
        var jsonStateDescriptions = json.getOptionalObject( "stateDescription" );
        if( jsonStateDescriptions != null ) {
            _stateDescriptions = new StateDescriptions ( 
                    jsonStateDescriptions.getOptionalArray( "options" ) 
                );
        }
    }

    // Returns the command descriptions
    public function getCommandDescriptions() as CommandDescriptions? {
        return _commandDescriptions;
    }

    // Normalizes command options for a Switch item.
    //
    // A Switch item only supports ON and OFF. Unsupported commands are
    // removed. If the result is not exactly one ON and one OFF command,
    // the whole set is discarded.
    public static function normalizeSwitchCommandOptions( 
        commandOptions as JsonAdapterArray? 
    ) as JsonAdapterArray? {
        if( commandOptions == null ) {
            return null;
        }

        var filtered = new Array<JsonAdapter>[0];
        var hasOn = false;
        var hasOff = false;
        for( var i = 0; i < commandOptions.size(); i++ ) {
            var commandOption = commandOptions[i];
            var command = commandOption.getOptionalString( "command" );
            if( command.equals( "" ) ) {
                var optionLabel = commandOption.getOptionalString( "label" );
                throw new JsonParsingException(
                    optionLabel.equals( "" )
                    ? Application.loadResource( Rez.Strings.ErrorSwitchCommandMissing ) as String
                    : Lang.format(
                        Application.loadResource( Rez.Strings.ErrorSwitchCommandMissingWithLabel ) as String,
                        [ optionLabel ]
                    )
                );
            }
            if( command.equals( ITEM_STATE_ON ) ) {
                filtered.add( commandOption );
                hasOn = true;
            } else if( command.equals( ITEM_STATE_OFF ) ) {
                filtered.add( commandOption );
                hasOff = true;
            }
        }

        if( hasOn && hasOff && filtered.size() == 2 ) {
            return filtered;
        }
        return null;
    }

    // Returns the state descriptions
    public function getStateDescriptions() as StateDescriptions? {
        return _stateDescriptions;
    }

    // Look up one specific command in the command descriptions
    public function lookupCommandDescription( command as String ) as String? {
        if( _commandDescriptions != null ) {
            return _commandDescriptions.lookup( command );
        } else {
            return null;
        }
    }

    // Look up one specific state in the state descriptions
    public function lookupStateDescription( state as String ) as String? {
        if( _stateDescriptions != null ) {
            return _stateDescriptions.lookup( state );
        } else {
            return null;
        }
    }
}