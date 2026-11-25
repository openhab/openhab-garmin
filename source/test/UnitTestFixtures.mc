import Toybox.Lang;

/*
 * Sitemap JSON fixtures for the Run No Evil unit tests.
 *
 * These reproduce the REST model that openHAB serves for the E2E fixture
 * sitemaps (`tests/fixtures/openhab/conf/sitemaps/garmin_e2e.sitemap`), reduced
 * to the fields the app reads: the same element types, nesting, labels, ranges
 * and item shapes. They are NOT byte captures of an openHAB response -- the
 * E2E tier remains the authority on openHAB's serialization. The unit tier
 * only needs a structural equivalent to exercise the parser.
 *
 * The tests are annotated (:test), so the compiler removes them from debug and
 * release builds. Objects are built imperatively instead of with nested
 * dictionary literals because the JSON typedefs are invariant.
 */
(:test)
class UnitTestFixtures {

    // Builds the payload of GET /rest/sitemaps/garmin_e2e.
    public static function e2eHomepage() as JsonObject {
        var textFrame = frameWidget( "Text", textFrameWidgets() );

        var emptyFrame = frameWidget( "Empty", new JsonArray[0] );

        var switchWidget = {
            "type" => "Switch",
            "label" => "Light [ON]",
            "icon" => "light",
            "item" => item( "E2E_Light", "Switch", "ON" ),
            "mappings" => mappingOptions()
        } as JsonObject;

        var setpointWidget = {
            "type" => "Setpoint",
            "label" => "Setpoint",
            "item" => item( "E2E_Setpoint", "Number", "21" ),
            "minValue" => 16.0,
            "maxValue" => 30.0,
            "step" => 0.5
        } as JsonObject;

        var sliderWidget = {
            "type" => "Slider",
            "label" => "Slider Release Only",
            "item" => item( "E2E_Slider", "Dimmer", "45.5" ),
            "minValue" => 0.0,
            "maxValue" => 50.0,
            "step" => 0.5,
            "releaseOnly" => true
        } as JsonObject;

        var widgets = new JsonArray[0];
        widgets.add( textFrame );
        widgets.add( emptyFrame );
        widgets.add( switchWidget );
        widgets.add( setpointWidget );
        widgets.add( sliderWidget );

        var homepage = {
            "id" => "garmin_e2e",
            "title" => "My Home",
            "widgets" => widgets
        } as JsonObject;

        var root = {} as JsonObject;
        root["name"] = "garmin_e2e";
        root["label"] = "My Home";
        root["homepage"] = homepage;
        return root;
    }

    // Children of the Frame labelled "Text": static texts, an item-backed text
    // with a server-side display state, and a text with a linked page.
    private static function textFrameWidgets() as JsonArray {
        var widgets = new JsonArray[0];

        widgets.add( textWidget( "Temperature", item( "E2E_Temperature", "Number", "21.5" ) ) );
        widgets.add( textWidget( "TV [Channel: BBC]", item( "E2E_TV", "String", "BBC" ) ) );
        widgets.add( textWidget( "This text has no item", null ) );

        var nested = textWidget( "Nested page", null );
        var nestedWidgets = new JsonArray[0];
        nestedWidgets.add( textWidget( "Nested static text", null ) );
        nestedWidgets.add( textWidget( "Nested temperature", item( "E2E_Temperature", "Number", "21.5" ) ) );
        nested["linkedPage"] = page( "Nested page", nestedWidgets );
        widgets.add( nested );

        return widgets;
    }

    public static function frameWidget( label as String, widgets as JsonArray ) as JsonObject {
        var frame = {} as JsonObject;
        frame["type"] = "Frame";
        frame["label"] = label;
        frame["widgets"] = widgets;
        return frame;
    }

    public static function textWidget( label as String, widgetItem as JsonObject? ) as JsonObject {
        var text = {} as JsonObject;
        text["type"] = "Text";
        text["label"] = label;
        if( widgetItem != null ) {
            text["item"] = widgetItem;
        }
        return text;
    }

    public static function page( title as String, widgets as JsonArray ) as JsonObject {
        var linkedPage = {} as JsonObject;
        linkedPage["id"] = title;
        linkedPage["title"] = title;
        linkedPage["widgets"] = widgets;
        return linkedPage;
    }

    public static function item( name as String, type as String, state as String ) as JsonObject {
        var widgetItem = {} as JsonObject;
        widgetItem["name"] = name;
        widgetItem["type"] = type;
        widgetItem["state"] = state;
        return widgetItem;
    }

    // Widget-level mappings, as openHAB serializes `mappings=[...]`.
    public static function mappingOptions() as JsonArray {
        var mappings = new JsonArray[0];
        var on = {} as JsonObject;
        on["command"] = "ON";
        on["label"] = "On";
        mappings.add( on );
        var off = {} as JsonObject;
        off["command"] = "OFF";
        off["label"] = "Off";
        mappings.add( off );
        return mappings;
    }

    // Homepage wrapper around an arbitrary widget list, used by the error tests.
    public static function homepageWith( title as String, widgets as JsonArray ) as JsonObject {
        var homepage = {} as JsonObject;
        homepage["id"] = "test";
        homepage["title"] = title;
        homepage["widgets"] = widgets;
        var root = {} as JsonObject;
        root["homepage"] = homepage;
        return root;
    }
}
