import Toybox.Lang;
import Toybox.Test;

/*
 * Regression tests for Text sitemap elements without an item.
 * openHAB allows static Text elements, which must load without an error
 * and display only their label.
 */

(:test)
function testStaticTextWithoutItem( logger as Test.Logger ) as Boolean {
    var json = new JsonAdapter( {
        "type" => "Text",
        "label" => "Static text"
    } as JsonObject );
    var text = new SitemapText( json, true, new SyncTaskQueue() );
    Test.assertEqual( "Static text", text.getLabel() );
    Test.assert( text.getItem() == null );
    Test.assert( text.getDisplayStateOrNull() == null );
    Test.assert( text.getLinkedPage() == null );
    return true;
}

(:test)
function testStaticTextWithLinkedPage( logger as Test.Logger ) as Boolean {
    var json = new JsonAdapter( {
        "type" => "Text",
        "label" => "Static text with nested page",
        "linkedPage" => {
            "type" => "Page",
            "title" => "Nested",
            "widgets" => [ { "type" => "Text", "label" => "Nested static text" } ]
        } as JsonObject
    } as JsonObject );
    var text = new SitemapText( json, true, new SyncTaskQueue() );
    Test.assert( text.getItem() == null );
    Test.assert( text.getLinkedPage() != null );
    var page = text.getLinkedPage();
    if( page != null ) {
        Test.assert( page.hasWidgets() );
    }
    return true;
}

(:test)
function testTextWithItem( logger as Test.Logger ) as Boolean {
    var json = new JsonAdapter( {
        "type" => "Text",
        "label" => "Text with item [%s]",
        "item" => {
            "name" => "TestSwitch",
            "type" => "Switch",
            "state" => "ON"
        } as JsonObject
    } as JsonObject );
    var text = new SitemapText( json, true, new SyncTaskQueue() );
    Test.assertEqual( "Text with item", text.getLabel() );
    Test.assertEqual( "%s", text.getDisplayState() );
    Test.assert( text.getItem() != null );
    return true;
}

(:test)
function testTextWithInvalidItemNamesLabel( logger as Test.Logger ) as Boolean {
    var json = new JsonAdapter( {
        "type" => "Text",
        "label" => "Broken text",
        "item" => { "type" => "Switch" } as JsonObject
    } as JsonObject );
    try {
        new SitemapText( json, true, new SyncTaskQueue() );
    } catch( ex ) {
        var message = ex.getErrorMessage();
        Test.assert( message != null );
        if( message != null ) {
            Test.assert( message.find( "Text 'Broken text'" ) != null );
        }
        return true;
    }
    Test.assert( false );
    return false;
}

(:test)
function testPageWithMixedStaticAndStatefulText( logger as Test.Logger ) as Boolean {
    var json = new JsonAdapter( {
        "title" => "Text Test",
        "widgets" => [
            {
                "type" => "Text",
                "label" => "Static text without item"
            } as JsonObject,
            {
                "type" => "Text",
                "label" => "Text with item [%s]",
                "item" => {
                    "name" => "TestSwitch",
                    "type" => "Switch",
                    "state" => "ON"
                } as JsonObject
            } as JsonObject
        ]
    } as JsonObject );

    // A SyncTaskQueue processes the container tasks immediately,
    // so the page's widgets are available for inspection.
    var taskQueue = new SyncTaskQueue();
    var page = new SitemapPage( json, true, taskQueue );
    taskQueue.executeTasks();

    var widgets = page.getWidgets();
    Test.assertEqual( 2, widgets.size() );

    var staticText = widgets[0] as SitemapText;
    Test.assertEqual( "Static text without item", staticText.getLabel() );
    Test.assert( staticText.getItem() == null );
    Test.assert( staticText.getDisplayStateOrNull() == null );

    var statefulText = widgets[1] as SitemapText;
    Test.assertEqual( "Text with item", statefulText.getLabel() );
    Test.assert( statefulText.getItem() != null );
    Test.assertEqual( "%s", statefulText.getDisplayState() );
    return true;
}

