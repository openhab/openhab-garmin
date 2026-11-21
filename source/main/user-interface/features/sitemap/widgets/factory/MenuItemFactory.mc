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
     * The selection logic is centralized here so that menu item creation and
     * reuse during sitemap updates always apply the same rules. See
     * AddOrUpdateMenuItemTask.
     */
    public static function getMenuItemType( 
        sitemapWidget as SitemapWidget 
    ) as MenuItemType {
        if( sitemapWidget instanceof SitemapFrame
            || sitemapWidget instanceof SitemapGroup ) {
            return CONTAINER;
        } else if( sitemapWidget instanceof SitemapText ) {
            return TEXT;
        } else if( sitemapWidget instanceof SitemapNumeric ) {
            return NUMERIC;
        } else if( sitemapWidget instanceof SitemapSwitch ) {
            return getSwitchMenuItemType( sitemapWidget );
        } else {
            throw new JsonParsingException( 
                "Element '" + sitemapWidget.getLabel() + "' is not supported" 
            );
        }
    }

    /*
     * Determines the menu item type for Switch sitemap elements.
     *
     * The checks are ordered so that dedicated full-screen controls take
     * precedence. Switch items are normalized in the data layer to either no
     * mappings or exactly ON/OFF mappings. They use the generic switch only
     * when those mappings have descriptions; otherwise they use the toggle.
     *
     * The toggle is limited to Switch and Dimmer items, because only their
     * state models can be interpreted as binary without losing information.
     * All other item types with available commands use the generic switch,
     * including when their commands are an undescribed ON/OFF pair. Items
     * without commands are not supported.
     */
    private static function getSwitchMenuItemType( 
        sitemapSwitch as SitemapSwitch 
    ) as MenuItemType {
        var itemType = sitemapSwitch.getSwitchItem().getType();

        if( itemType.equals( "Player" ) ) {
            return PLAYER;
        } else if( itemType.equals( "Rollershutter" ) ) {
            return ROLLERSHUTTER;
        } else if( itemType.equals( "Switch" ) ) {
            // Switch items are normalized in the data layer to either no
            // mappings or exactly ON/OFF mappings. Use the generic switch
            // only when those mappings have descriptions.
            return sitemapSwitch.hasDescribedToggleSwitchMappings()
                ? GENERIC_SWITCH
                : ON_OFF_SWITCH;
        } else if( itemType.equals( "Dimmer" )
            && ( ! sitemapSwitch.hasMappings()
                 || sitemapSwitch.hasToggleSwitchMappingsWithoutDescriptions() ) ) {
            // A Dimmer uses the toggle when it has no commands or an undescribed
            // ON/OFF pair, because its numeric state maps to the binary model
            // (0 = off, 1-100 = on).
            return ON_OFF_SWITCH;
        } else if( sitemapSwitch.hasMappings() ) {
            return GENERIC_SWITCH;
        } else {
            throw new JsonParsingException( 
                "Element '" + sitemapSwitch.getLabel() + "' is not supported" 
            );
        }
    }

    /*
     * Determines whether a menu item is an instance of the class that
     * represents the given menu item type.
     *
     * Menu items are reused by position when a sitemap update is applied.
     * Reuse is only valid if the existing menu item is of the exact class
     * that would be created for the new sitemap element. This ensures that
     * a menu item is never reused for a different widget type.
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