import Toybox.Lang;
import Toybox.WatchUi;

/*
 * This menu item implementation represents a frame, a container 
 * that holds other menu items.
 * The item's label displays the Frame's label, and in onSelectImpl(), 
 * it opens a PageMenu representing the Frame's content.
 */
class ContainerMenuItem extends WidgetMenuItem {

    // Constructor
    // Initializes the base class and sets the menu view to open when this item is selected.
    public function initialize( 
        sitemapContainer as SitemapFrame or SitemapGroup, 
        parent as BasePageMenu,
        processingMode as BasePageMenu.ProcessingMode
    ) {
        WidgetMenuItem.initialize( { 
            :sitemapWidget => sitemapContainer,
            :stateTextResponsive => sitemapContainer.getDisplayStateOrNull(),
            :parent => parent,
            :processingMode => processingMode
        } );
    }

    // When the state changes, change the displayed state text
    public function onStateChanged() as Void {
        setStateTextResponsive( getSitemapWidget().getDisplayStateOrNull() );
    }

}