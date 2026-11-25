import Toybox.Lang;
import Toybox.Test;

/*
 * Unit tests for the sitemap JSON -> model tier.
 *
 * These exercise the app's own parser (`JsonAdapter`, `SitemapHomepage`,
 * `SitemapWidgetFactory`, the widget classes and `Item`) against a payload
 * shaped like the `garmin_e2e` E2E fixture. They need no view, no input and no
 * network: `SitemapHomepage` accepts a synchronous task queue, so the whole
 * model is built before the test inspects it.
 *
 * A failure here means the model no longer matches the openHAB REST contract
 * the E2E fixtures describe. It does not prove anything about rendering.
 */
(:test)
class SitemapModelTests {

    (:test)
    static function homepageTitleAndWidgetTypes( logger as Test.Logger ) as Boolean {
        var homepage = parse( UnitTestFixtures.e2eHomepage() );
        var widgets = homepage.getWidgets();
        logger.debug( "homepage title=" + homepage.title + " widgets=" + widgets.size() );

        Test.assertEqualMessage( "My Home", homepage.title, "homepage title" );
        Test.assertEqualMessage( 5, widgets.size(), "top level widget count" );

        Test.assertEqualMessage( "Frame", widgets[0].getType(), "widget 0 type" );
        Test.assertEqualMessage( "Frame", widgets[1].getType(), "widget 1 type" );
        Test.assertEqualMessage( "Switch", widgets[2].getType(), "widget 2 type" );
        Test.assertEqualMessage( "Setpoint", widgets[3].getType(), "widget 3 type" );
        Test.assertEqualMessage( "Slider", widgets[4].getType(), "widget 4 type" );
        return true;
    }

    (:test)
    static function labelStateIsSplitFromLabel( logger as Test.Logger ) as Boolean {
        var textFrame = parse( UnitTestFixtures.e2eHomepage() ).getWidgets()[0];
        var page = requirePage( textFrame );
        var widgets = page.getWidgets();

        var temperature = widgets[0];
        var channel = widgets[1];
        logger.debug( "temperature label=" + temperature.getLabel()
            + " state=" + temperature.getDisplayState() );
        logger.debug( "channel label=" + channel.getLabel()
            + " state=" + channel.getDisplayState() );

        // Without a server-side display state the item state is shown.
        Test.assertEqualMessage( "Temperature", temperature.getLabel(), "temperature label" );
        Test.assertEqualMessage( "21.5", temperature.getDisplayState(), "temperature state" );
        Test.assertMessage( ! temperature.hasRemoteDisplayState(), "no remote state expected" );

        // "TV [Channel: BBC]" is split into label and display state.
        Test.assertEqualMessage( "TV", channel.getLabel(), "channel label" );
        Test.assertEqualMessage( "Channel: BBC", channel.getDisplayState(), "channel state" );
        Test.assertEqualMessage( "Channel: BBC", channel.getRemoteDisplayState(), "channel remote state" );

        // A Text element without an item has neither a state nor an item.
        Test.assertMessage( widgets[2].getItem() == null, "static text must have no item" );
        Test.assertMessage( ! widgets[2].hasDisplayState(), "static text must have no display state" );
        return true;
    }

    (:test)
    static function framesAndLinkedPagesAreBuilt( logger as Test.Logger ) as Boolean {
        var widgets = parse( UnitTestFixtures.e2eHomepage() ).getWidgets();

        // Frame children are at the same JSON level ("widgets").
        var textFramePage = requirePage( widgets[0] );
        logger.debug( "text frame children=" + textFramePage.getWidgets().size() );
        Test.assertMessage( textFramePage.hasWidgets(), "text frame must have children" );
        Test.assertEqualMessage( 4, textFramePage.getWidgets().size(), "text frame child count" );

        // Text children are a "linkedPage" object.
        var nestedPage = requirePage( textFramePage.getWidgets()[3] );
        Test.assertMessage( nestedPage.hasWidgets(), "nested page must have children" );
        Test.assertEqualMessage( 2, nestedPage.getWidgets().size(), "nested page child count" );

        // An empty Frame keeps its page but has no widgets: that is the
        // condition the UI turns into the "page is empty" toast.
        var emptyPage = requirePage( widgets[1] );
        Test.assertMessage( ! emptyPage.hasWidgets(), "empty frame must have no children" );
        Test.assertEqualMessage( 0, emptyPage.getWidgets().size(), "empty frame child count" );
        return true;
    }

    (:test)
    static function unsupportedElementTypeIsRejected( logger as Test.Logger ) as Boolean {
        var widget = {} as JsonObject;
        widget["type"] = "Chart";
        widget["label"] = "Chart";

        var message = catchParsingMessage( widget );
        logger.debug( "unsupported type message=" + message );
        Test.assertMessage( message != null, "an unsupported type must raise JsonParsingException" );
        Test.assertMessage( ( message as String ).find( "Chart" ) != null,
            "the message must name the offending element" );
        return true;
    }

    (:test)
    static function emptyHomepageIsRejected( logger as Test.Logger ) as Boolean {
        var message = parseAndCatch( UnitTestFixtures.homepageWith( "Empty", new JsonArray[0] ) );
        logger.debug( "empty homepage message=" + message );
        Test.assertMessage( message != null, "an empty homepage must raise JsonParsingException" );
        return true;
    }

    (:test)
    static function itemTypeStateAndUnitRules( logger as Test.Logger ) as Boolean {
        // A Group reports the groupType as its type.
        var groupJson = UnitTestFixtures.item( "E2E_Group", "Group", "ON" );
        groupJson["groupType"] = "Dimmer";
        var group = new Item( new JsonAdapter( groupJson ), true );
        Test.assertEqualMessage( "Dimmer", group.getType(), "group type comes from groupType" );
        Test.assertEqualMessage( "%", group.getUnit(), "Dimmer unit is always %" );

        // A stale sitemap hides the state, even when the JSON carries one.
        var stale = new Item(
            new JsonAdapter( UnitTestFixtures.item( "E2E_Light", "Switch", "ON" ) ),
            false
        );
        logger.debug( "stale state=" + stale.getState() );
        Test.assertMessage( ! stale.hasState(), "a stale sitemap must not expose item states" );
        Test.assertEqualMessage( Item.NO_STATE, stale.getState(), "stale state placeholder" );

        // NULL and UNDEF are normalized to the no-state placeholder as well.
        var undef = new Item(
            new JsonAdapter( UnitTestFixtures.item( "E2E_Light", "Switch", "UNDEF" ) ),
            true
        );
        Test.assertMessage( ! undef.hasState(), "UNDEF must be normalized" );
        return true;
    }

    private static function parse( json as JsonObject ) as SitemapHomepage {
        return new SitemapHomepage( new JsonAdapter( json ), true, false );
    }

    private static function requirePage( element as SitemapWidget ) as SitemapContainer {
        var page = element.getLinkedPage();
        if( page == null ) {
            throw new JsonParsingException( "expected a linked page for " + element.getType() );
        }
        return ( page as SitemapContainer );
    }

    private static function parseAndCatch( json as JsonObject ) as String? {
        try {
            parse( json );
        } catch( ex instanceof JsonParsingException ) {
            return ex.getErrorMessage() as String;
        }
        return null;
    }

    private static function catchParsingMessage( widget as JsonObject ) as String? {
        try {
            SitemapWidgetFactory.createByType( new JsonAdapter( widget ), true, new SyncTaskQueue() );
        } catch( ex instanceof JsonParsingException ) {
            return ex.getErrorMessage() as String;
        }
        return null;
    }
}
