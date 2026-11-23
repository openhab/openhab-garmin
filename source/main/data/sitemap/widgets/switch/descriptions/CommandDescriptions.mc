import Toybox.Lang;
import Toybox.Application;

public class CommandDescriptions extends BaseDescriptions {

    public function initialize( jsonCommandDescriptions as JsonAdapterArray? ) {
        var commandDescriptions = new Array<CommandDescription>[0];
        if( jsonCommandDescriptions != null ) {
            for( var i = 0; i < jsonCommandDescriptions.size(); i++ ) {
                var jsonCommandDescription = jsonCommandDescriptions[i];
                var command = jsonCommandDescription.getOptionalString( "command" );
                if( command.equals( "" ) ) {
                    var errorLabel = jsonCommandDescription.getOptionalString( "label" );
                    throw new JsonParsingException(
                        errorLabel.equals( "" )
                        ? Application.loadResource( Rez.Strings.ErrorSwitchCommandMissing ) as String
                        : Lang.format(
                            Application.loadResource( Rez.Strings.ErrorSwitchCommandMissingWithLabel ) as String,
                            [ errorLabel ]
                        )
                    );
                }
                var label = jsonCommandDescription.getOptionalString( "label" );
                commandDescriptions.add( 
                    new CommandDescription( command, label )
                );
            }
        }
        BaseDescriptions.initialize( commandDescriptions as Array<BaseDescription> );
    }

    public function getCommandDescription( i as Number ) as CommandDescription {
        return get( i ) as CommandDescription;
    }

}