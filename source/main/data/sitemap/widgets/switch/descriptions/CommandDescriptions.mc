import Toybox.Lang;
import Toybox.Application;

public class CommandDescriptions extends BaseDescriptions {

    public function initialize( jsonCommandDescriptions as JsonAdapterArray? ) {
        var commandDescriptions = new Array<CommandDescription>[0];
        if( jsonCommandDescriptions != null ) {
            for( var i = 0; i < jsonCommandDescriptions.size(); i++ ) {
                var jsonCommandDescription = jsonCommandDescriptions[i];
                commandDescriptions.add( 
                    new CommandDescription( 
                        jsonCommandDescription.getString(
                            "command",
                            Application.loadResource( Rez.Strings.ErrorSwitchCommandMissing ) as String
                        ),
                        jsonCommandDescription.getOptionalString( "label" )
                    )
                );
            }
        }
        BaseDescriptions.initialize( commandDescriptions as Array<BaseDescription> );
    }

    public function getCommandDescription( i as Number ) as CommandDescription {
        return get( i ) as CommandDescription;
    }

}