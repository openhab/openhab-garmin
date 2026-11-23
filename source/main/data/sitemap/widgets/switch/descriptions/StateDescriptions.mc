import Toybox.Lang;
import Toybox.Application;

public class StateDescriptions extends BaseDescriptions {

    public function initialize( jsonStateDescriptions as JsonAdapterArray? ) {
        var stateDescriptions = new Array<StateDescription>[0];
        if( jsonStateDescriptions != null ) {
            for( var i = 0; i < jsonStateDescriptions.size(); i++ ) {
                var jsonStateDescription = jsonStateDescriptions[i];
                var value = jsonStateDescription.getOptionalString( "value" );
                if( value.equals( "" ) ) {
                    var errorLabel = jsonStateDescription.getOptionalString( "label" );
                    throw new JsonParsingException(
                        errorLabel.equals( "" )
                        ? Application.loadResource( Rez.Strings.ErrorSwitchValueMissing ) as String
                        : Lang.format(
                            Application.loadResource( Rez.Strings.ErrorSwitchValueMissingWithLabel ) as String,
                            [ errorLabel ]
                        )
                    );
                }
                var label = jsonStateDescription.getOptionalString( "label" );
                if( label.equals( "" ) ) {
                    throw new JsonParsingException( Lang.format(
                        Application.loadResource( Rez.Strings.ErrorSwitchLabelMissingWithValue ) as String,
                        [ value ]
                    ) );
                }
                stateDescriptions.add(
                    new StateDescription( value, label )
                );
            }
        }
        BaseDescriptions.initialize( stateDescriptions as Array<BaseDescription> );
    }
}