import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Class representing `Text` elements.
 *
 * The item is optional. openHAB allows static `Text` elements without an
 * item, which display only their label. If an item is present, its state
 * is displayed as defined by the sitemap label. All other functionality,
 * such as nested pages, is provided by the base class.
 */
class SitemapText extends SitemapWidget {

    public function initialize( 
        json as JsonAdapter, 
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) {
        var item = null as Item?;

        // Obtain the item part of the element, if present.
        // A static Text element without an item displays only its label.
        try {
            var jsonItem = json.getOptionalObject( "item" );
            if( jsonItem != null ) {
                item = new Item( jsonItem, isSitemapFresh );
            }
        } catch( ex ) {
            // The item does not have the type/label, so we add it to any
            // exception thrown when creating the item. To be able to
            // access the type/label, we need to initialize the base class
            SitemapWidget.initialize( json, null, null, isSitemapFresh, taskQueue );
            throw new JsonParsingException(
                getType() + " '" + getLabel() + "': " + ex.getErrorMessage() );
        }

        // The base class relies on the item for parsing the icon, 
        // therefore we initialize it after the item was created.
        // The item may be null for static Text elements.
        SitemapWidget.initialize( 
            json, 
            item,
            null,
            isSitemapFresh,
            taskQueue
        );
    }
}