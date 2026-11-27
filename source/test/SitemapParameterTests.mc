import Toybox.Lang;
import Toybox.Test;

/*
 * Unit tests for sitemap element parameters.
 *
 * These cover the values that the UI consumes but that are computed from the
 * JSON alone: Setpoint/Slider ranges and validation, releaseOnly, the numeric
 * display format, colors, and the ON/OFF mapping normalization of Switch
 * elements. Colors and ranges are the two parameter groups the E2E suite
 * currently waives (they need goldens), so this is the tier that can assert
 * them without pixels.
 */
(:test)
class SitemapParameterTests {

    (:test)
    static function numericDefaultsAndRanges( logger as Test.Logger ) as Boolean {
        var widget = numericWidget( "Setpoint", "Number", "50", null, null, null );

        var setpoint = SitemapWidgetFactory.createByType(
            new JsonAdapter( widget ), true, new SyncTaskQueue()
        ) as SitemapNumeric;
        logger.debug( "defaults min=" + setpoint.getMinValue() + " max=" + setpoint.getMaxValue()
            + " step=" + setpoint.getStep() );
        Test.assertMessage( setpoint.getMinValue() == 0.0, "default minValue is 0" );
        Test.assertMessage( setpoint.getMaxValue() == 100.0, "default maxValue is 100" );
        Test.assertMessage( setpoint.getStep() == 1.0, "default step is 1" );

        var explicit = numericWidget( "Slider", "Dimmer", "40", 16.0, 30.0, 0.5 );
        var slider = SitemapWidgetFactory.createByType(
            new JsonAdapter( explicit ), true, new SyncTaskQueue()
        ) as SitemapNumeric;
        Test.assertMessage( slider.getMinValue() == 16.0, "minValue is read from the JSON" );
        Test.assertMessage( slider.getMaxValue() == 30.0, "maxValue is read from the JSON" );
        Test.assertMessage( slider.getStep() == 0.5, "step is read from the JSON" );
        return true;
    }

    /*
     * These are the documented preconditions for the picker: a step of zero
     * would move away from maxValue, an inverted range has no pickable value,
     * and more than UI_NUMERIC_MAX_STEPS steps are not supported. All three
     * must be rejected while parsing, not while the user is picking.
     */
    (:test)
    static function invalidNumericParametersAreRejected( logger as Test.Logger ) as Boolean {
        var cases = new Array<JsonObject>[0];
        cases.add( numericWidget( "Setpoint", "Number", "20", 0.0, 100.0, 0.0 ) );
        cases.add( numericWidget( "Setpoint", "Number", "20", 0.0, 100.0, -1.0 ) );
        cases.add( numericWidget( "Setpoint", "Number", "20", 100.0, 0.0, 1.0 ) );
        cases.add( numericWidget( "Slider", "Dimmer", "20", 0.0, 10000.0, 1.0 ) );

        for( var i = 0; i < cases.size(); i++ ) {
            var rejected = numericIsRejected( cases[i] );
            logger.debug( "case " + i + " rejected=" + rejected );
            Test.assertMessage( rejected, "invalid numeric parameters were accepted (case " + i + ")" );
        }
        return true;
    }

    /*
     * Without a state a Slider has nothing to cancel back to, so releaseOnly
     * is forced on. With a state the parameter is honoured, defaulting off.
     */
    (:test)
    static function releaseOnlyDefaults( logger as Test.Logger ) as Boolean {
        var withoutState = numericWidget( "Slider", "Dimmer", null, 0.0, 50.0, 0.5 );
        var forced = SitemapWidgetFactory.createByType(
            new JsonAdapter( withoutState ), true, new SyncTaskQueue()
        ) as SitemapNumeric;
        Test.assertMessage( forced.isReleaseOnly(), "releaseOnly must default on without a state" );

        var withState = numericWidget( "Slider", "Dimmer", "40.5", 0.0, 50.0, 0.5 );
        var off = SitemapWidgetFactory.createByType(
            new JsonAdapter( withState ), true, new SyncTaskQueue()
        ) as SitemapNumeric;
        Test.assertMessage( ! off.isReleaseOnly(), "releaseOnly must default off with a state" );
        return true;
    }

    (:test)
    static function numericDisplayStateUsesValueAndUnit( logger as Test.Logger ) as Boolean {
        var json = numericWidget( "Slider", "Dimmer", "45.5", 0.0, 50.0, 0.5 );
        var slider = SitemapWidgetFactory.createByType(
            new JsonAdapter( json ), true, new SyncTaskQueue()
        ) as SitemapNumeric;
        logger.debug( "slider display state=" + slider.getDisplayState() );

        Test.assertEqualMessage( "46%", slider.getDisplayState(),
            "a fractional Dimmer state takes the % unit without decimal places" );
        Test.assertEqualMessage( "45.5", slider.formatState( 45.5 ), "formatState uses the step decimals" );
        return true;
    }

    (:test)
    static function switchMappingsAreNormalized( logger as Test.Logger ) as Boolean {
        var complete = SwitchItem.normalizeSwitchCommandOptions( commandOptions(
            [ "ON", "OFF" ]
        ) );
        Test.assertMessage( complete != null, "a complete ON/OFF set must be kept" );
        Test.assertEqualMessage( 2, ( complete as JsonAdapterArray ).size(), "mapping count" );

        // Commands beyond ON/OFF are dropped; ON/OFF survives.
        var mixed = SwitchItem.normalizeSwitchCommandOptions( commandOptions(
            [ "ON", "OFF", "UP" ]
        ) );
        Test.assertMessage( mixed != null, "extra commands must be dropped, not the set" );
        Test.assertEqualMessage( 2, ( mixed as JsonAdapterArray ).size(), "normalized mapping count" );

        // An incomplete set is discarded entirely.
        var incomplete = SwitchItem.normalizeSwitchCommandOptions( commandOptions( [ "ON" ] ) );
        Test.assertMessage( incomplete == null, "an incomplete ON/OFF set must be discarded" );

        // A mapping without a command is a parse error.
        var missing = new JsonAdapterArray[0];
        var broken = {} as JsonObject;
        broken["label"] = "Broken";
        missing.add( new JsonAdapter( broken ) );
        var rejected = false;
        try {
            SwitchItem.normalizeSwitchCommandOptions( missing );
        } catch( ex instanceof JsonParsingException ) {
            rejected = true;
        }
        logger.debug( "mapping without command rejected=" + rejected );
        Test.assertMessage( rejected, "a mapping without a command must be rejected" );
        return true;
    }

    (:test)
    static function switchDisplayStatePrefersWidgetMappings( logger as Test.Logger ) as Boolean {
        var homepage = new SitemapHomepage(
            new JsonAdapter( UnitTestFixtures.e2eHomepage() ), true, false
        );
        var widget = homepage.getWidgets()[2];
        var sitemapSwitch = widget as SitemapSwitch;
        logger.debug( "switch display state=" + sitemapSwitch.getDisplayState() );

        Test.assertEqualMessage( "On", sitemapSwitch.getDisplayState(),
            "the sitemap mapping wins over the raw item state" );
        Test.assertMessage( sitemapSwitch.hasToggleSwitchMappings(),
            "ON/OFF mappings must be detected as a toggle switch" );
        Test.assertEqualMessage( 2, sitemapSwitch.getCommandDescriptions().size(),
            "both mappings must be kept" );
        return true;
    }

    (:test)
    static function colorsAreParsed( logger as Test.Logger ) as Boolean {
        var widget = {} as JsonObject;
        widget["type"] = "Text";
        widget["label"] = "Colors";
        widget["labelcolor"] = "red";
        widget["valuecolor"] = "#0a0b0c";

        var text = SitemapWidgetFactory.createByType(
            new JsonAdapter( widget ), true, new SyncTaskQueue()
        );
        logger.debug( "label color=" + text.getLabelColor() + " value color=" + text.getValueColor() );
        Test.assertMessage( text.getLabelColor() == 0xff0000, "a named color must be mapped" );
        Test.assertMessage( text.getValueColor() == 0x0a0b0c, "a hex color must be parsed" );

        // An unknown color is a parse error, not a silent fallback.
        var invalid = {} as JsonObject;
        invalid["type"] = "Text";
        invalid["label"] = "Broken color";
        invalid["labelcolor"] = "notacolor";
        var rejected = false;
        try {
            SitemapWidgetFactory.createByType( new JsonAdapter( invalid ), true, new SyncTaskQueue() );
        } catch( ex instanceof JsonParsingException ) {
            rejected = true;
        }
        Test.assertMessage( rejected, "an unknown color must be rejected" );
        return true;
    }

    // Builds a Setpoint/Slider widget. Null bounds are omitted so that the
    // defaults can be asserted; a null state omits the item state.
    private static function numericWidget(
        type as String,
        itemType as String,
        state as String?,
        minValue as Float?,
        maxValue as Float?,
        step as Float?
    ) as JsonObject {
        var widget = {} as JsonObject;
        widget["type"] = type;
        widget["label"] = type;
        var widgetItem = UnitTestFixtures.item( "E2E_" + type, itemType, state == null ? "NULL" : state );
        if( state == null ) {
            widgetItem["state"] = null;
        }
        widget["item"] = widgetItem;
        if( minValue != null ) {
            widget["minValue"] = minValue;
        }
        if( maxValue != null ) {
            widget["maxValue"] = maxValue;
        }
        if( step != null ) {
            widget["step"] = step;
        }
        return widget;
    }

    private static function numericIsRejected( widget as JsonObject ) as Boolean {
        try {
            SitemapWidgetFactory.createByType( new JsonAdapter( widget ), true, new SyncTaskQueue() );
        } catch( ex instanceof JsonParsingException ) {
            return true;
        }
        return false;
    }

    private static function commandOptions( commands as Array<String> ) as JsonAdapterArray {
        var options = new JsonAdapterArray[0];
        for( var i = 0; i < commands.size(); i++ ) {
            var option = {} as JsonObject;
            option["command"] = commands[i];
            options.add( new JsonAdapter( option ) );
        }
        return options;
    }
}
