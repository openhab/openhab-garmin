import Toybox.Lang;
import Toybox.Application;

public class StateDescriptions extends BaseDescriptions {

    public function initialize( jsonStateDescriptions as JsonAdapterArray? ) {
        var stateDescriptions = new Array<StateDescription>[0];
        if( jsonStateDescriptions != null ) {
            for( var i = 0; i < jsonStateDescriptions.size(); i++ ) {
                var jsonStateDescription = jsonStateDescriptions[i];
                stateDescriptions.add( 
                    new StateDescription( 
                        jsonStateDescription.getString(
                            "value",
                            Application.loadResource( Rez.Strings.ErrorSwitchValueMissing ) as String
                        ),
                        jsonStateDescription.getString(
                            "label",
                            Application.loadResource( Rez.Strings.ErrorSwitchLabelMissing ) as String
                        )
                ) );
            }
        }
        BaseDescriptions.initialize( stateDescriptions as Array<BaseDescription> );
    }
}