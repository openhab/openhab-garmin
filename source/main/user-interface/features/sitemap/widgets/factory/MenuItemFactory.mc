import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Factory class for creating the appropriate object 
 * based on a given sitemap element.
 */
class MenuItemFactory {

    // Identifies the menu item class used to represent a sitemap element.
    // See getMenuItemType() for the order of the corresponding checks.
    public enum MenuItemType {
        CONTAINER,
        ON_OFF_SWITCH,
        TEXT,
        NUMERIC,
        ROLLERSHUTTER,
        PLAYER,
        GENERIC_SWITCH
    }

    /*
     * Determines the menu item class that represents the given sitemap element.
     *
     * The checks are ordered from the most specific to the most generic class,
     * because the isMyType() predicates of the menu item classes are not
     * mutually exclusive. For example, a Switch for a Player item satisfies
     * both PlayerMenuItem.isMyType() and GenericSwitchMenuItem.isMyType().
     *
     * This function is the single source of truth for the type selection. It is
     * used both when a menu item is created and when an existing menu item is
     * checked for reuse during a sitemap update. See AddOrUpdateMenuItemTask.
     */
    public static function getMenuItemType( 
        sitemapWidget as SitemapWidget 
    ) as MenuItemType {
        if( ContainerMenuItem.isMyType( sitemapWidget ) ) {
            return CONTAINER;
        } else if( OnOffSwitchMenuItem.isMyType( sitemapWidget ) ) {
            return ON_OFF_SWITCH;
        } else if( TextMenuItem.isMyType( sitemapWidget ) ) {
            return TEXT;
        } else if( NumericMenuItem.isMyType( sitemapWidget ) ) {
            return NUMERIC;
        } else if( RollershutterMenuItem.isMyType( sitemapWidget ) ) {
            return ROLLERSHUTTER;
        } else if( PlayerMenuItem.isMyType( sitemapWidget ) ) {
            return PLAYER;
        } else if( GenericSwitchMenuItem.isMyType( sitemapWidget ) ) {
            return GENERIC_SWITCH;
        } else {
            throw new JsonParsingException( 
                "Element '" + sitemapWidget.getLabel() + "' is not supported" 
            );
        }
    }

    /*
     * Determines whether a menu item is an instance of the class that
     * represents the given menu item type.
     *
     * Menu items are reused by position when a sitemap update is applied.
     * Reuse is only valid if the existing menu item is of the class that
     * would be created for the new sitemap element. Checking isMyType()
     * alone is not sufficient because those predicates overlap between
     * classes (e.g. GenericSwitchMenuItem accepts a Player switch as well).
     */
    public static function isMenuItemType( 
        menuItem as WidgetMenuItem, 
        type as MenuItemType 
    ) as Boolean {
        switch( type ) {
            case CONTAINER:      return menuItem instanceof ContainerMenuItem;
            case ON_OFF_SWITCH:  return menuItem instanceof OnOffSwitchMenuItem;
            case TEXT:           return menuItem instanceof TextMenuItem;
            case NUMERIC:        return menuItem instanceof NumericMenuItem;
            case ROLLERSHUTTER:  return menuItem instanceof RollershutterMenuItem;
            case PLAYER:         return menuItem instanceof PlayerMenuItem;
            case GENERIC_SWITCH: return menuItem instanceof GenericSwitchMenuItem;
            default:             return false;
        }
    }

    // Creates a menu item based on the sitemap element type
    public static function createMenuItem( 
        sitemapWidget as SitemapWidget, 
        parent as BasePageMenu,
        processingMode as BasePageMenu.ProcessingMode
    ) as WidgetMenuItem {
        
        // Check if there is still sufficent memory, otherwise
        // ensureSufficientMemory() will throw an exception
        MemoryManager.ensureSufficientMemory();

        switch( getMenuItemType( sitemapWidget ) ) {
            case CONTAINER:
                return new ContainerMenuItem( 
                    sitemapWidget as SitemapFrame or SitemapGroup, 
                    parent,
                    processingMode );
            case ON_OFF_SWITCH:
                return new OnOffSwitchMenuItem( 
                    sitemapWidget as SitemapSwitch, parent, processingMode );
            case TEXT:
                return new TextMenuItem( 
                    sitemapWidget as SitemapText, parent, processingMode );
            case NUMERIC:
                return new NumericMenuItem( 
                    sitemapWidget as SitemapNumeric, parent, processingMode );
            case ROLLERSHUTTER:
                return new RollershutterMenuItem( 
                    sitemapWidget as SitemapSwitch, parent, processingMode );
            case PLAYER:
                return new PlayerMenuItem( 
                    sitemapWidget as SitemapSwitch, parent, processingMode );
            case GENERIC_SWITCH:
                return new GenericSwitchMenuItem( 
                    sitemapWidget as SitemapSwitch, parent, processingMode );
            default:
                throw new GeneralException( "Unsupported menu item type" );
        }
    }
}