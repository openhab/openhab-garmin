import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Menu item implementation for `Text` sitemap elements.
 * The text state is displayed in the state field of StructuredMenuItem.
 * The state is extracted from the sitemap element's label, which contains
 * both the label text and the state enclosed in square brackets.
 * This approach is preferred over retrieving the state from the associated item,
 * as the embedded state includes any formatting defined for the sitemap text element.
 *
 * A static Text element has no item and therefore no state. In that case no state
 * text is displayed, so the no-state marker is omitted.
 */
class TextMenuItem extends WidgetMenuItem {
    
    // Constructor
    public function initialize( 
        sitemapText as SitemapText,
        parent as BasePageMenu,
        processingMode as BasePageMenu.ProcessingMode
    ) {
        WidgetMenuItem.initialize( {
            :sitemapWidget => sitemapText,
            :stateTextResponsive => sitemapText.getDisplayStateOrNull(),
            :parent => parent,
            :processingMode => processingMode
        } );
    }

    // When the state changes, change the displayed state text
    public function onStateChanged() as Void {
        setStateTextResponsive( getSitemapWidget().getDisplayStateOrNull() );
    }

}