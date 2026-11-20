import Toybox.Lang;
import Toybox.WatchUi;

/*
 * This file contains all classes representing asynchronous tasks used to
 * update the menu structure based on a new sitemap.
 *
 * The update process involves the following tasks:
 *
 * 1) AddOrUpdateMenuItemTask
 *    Processes a single sitemap element, updating the corresponding menu item
 *    by ID or adding it if it does not yet exist. See BasePageMenu.update() for details.
 *
 * 2) DeleteUnusedMenuItemsTask
 *    Removes any menu items that no longer have a corresponding sitemap element.
 *
 * 3) SwitchViewIfVisibleAndItemCountChangedTask
 *    Implements a workaround for a Garmin SDK bug. See the class description for details.
 */

/*
 * Task for updating the menu item with the ID of a given sitemap element,
 * or adding it if it does not already exist.
 *
 * Sitemap-assigned identifiers are effectively positional indexes. When a new element is 
 * inserted in the middle of a page, it receives the identifier previously assigned to the 
 * item at that position, and subsequent items are reindexed accordingly.
 *
 * Therefore this task has to deal with different cases:
 *   - If the types match, the item is updated in place.
 *   - If the types differ, the existing item is replaced.
 *   - If no matching menu item exists for a given index, a new one is added.
 * 
 * See also BasePageMenu.update() for further information on the update algorithm.
 */
class AddOrUpdateMenuItemTask extends BaseSitemapProcessorTask {
    private var _index as Number;
    private var _sitemapWidget as SitemapWidget;
    private var _pageMenu as BasePageMenu;

    // Constructor
    public function initialize( 
        index as Number,
        sitemapWidget as SitemapWidget,
        pageMenu as BasePageMenu 
    ) {
        BaseSitemapProcessorTask.initialize();
        _index = index;
        _sitemapWidget = sitemapWidget;
        _pageMenu = pageMenu;
    }
    
    // Add the element
    public function invoke() as Void {
        if( _index >= _pageMenu.getItemCount() ) {
            // If the item does not exist yet, we create it
            _pageMenu.addItem( 
                MenuItemFactory.createMenuItem( 
                    _sitemapWidget, 
                    _pageMenu,
                    BasePageMenu.PROCESSING_ASYNC
                ) 
            );
            // Logger.debug( "PageMenu.update: adding new item to page '" + _pageMenu.getLabel() + "'" );
        } else {
            // If the item is found, we check if the type of the menu
            // item is the same or has changed
            var item = _pageMenu.getItem( _index );

            // An existing menu item can only be reused if it is an instance
            // of the exact class that MenuItemFactory would create for the new
            // sitemap element. This prevents an item from silently changing
            // its representation when sitemap positions shift (e.g. due to
            // visibility changes).
            if( item instanceof WidgetMenuItem
                && MenuItemFactory.isMenuItemType( 
                    item, 
                    MenuItemFactory.getMenuItemType( _sitemapWidget ) 
                )
            ) {
                // If the type matches, the menu item is updated.
                // However, if the item was updated internally within the configured
                // post-command hold time, the update is ignored. Internal updates are used
                // by some menu items to immediately reflect the new state after a command
                // is sent. The sitemap request issued immediately after the command may
                // still return the old state. To avoid this, the post-command hold time
                // configured in the app settings locks the item against updates for a
                // defined period after sending a command.
                if( ! ( item instanceof CommandMenuItem && item.isInHoldTime() ) ) {
                    item.updateWidget( _sitemapWidget );
                }
            } else {
                // If the item is not a WidgetMenuItem or the widget type
                // does not match, create a new item and replace the
                // existing menu item.
                var newItem = MenuItemFactory.createMenuItem( 
                    _sitemapWidget, 
                    _pageMenu,
                    BasePageMenu.PROCESSING_ASYNC
                );

                // If either the previous or the new menu item has sub-pages,
                // invalidate the structure. This causes the app to jump back
                // to the homepage menu.
                if( ( item instanceof WidgetMenuItem && item.hasPage() ) 
                    || newItem.hasPage() 
                ) {
                    _pageMenu.invalidateStructure();
                    // Logger.debug( "AddOrUpdateMenuItemTask: page '" + _pageMenu.getTitle() + "' invalid because item '" + item.getLabel() + "' changed type from/to page" );
                }
                _pageMenu.updateItem( newItem, _index );
            }
        }
    }
}

/*
 * If any existing menu items remain after processing all sitemap elements, 
 * they are removed.
 */
class DeleteUnusedMenuItemsTask extends BaseSitemapProcessorTask {
    private var _pageMenu as BasePageMenu;
    private var _sitemapContainer as SitemapContainer;

    // Constructor
    public function initialize( 
        sitemapContainer as SitemapContainer,
        pageMenu as BasePageMenu 
    ) {
        BaseSitemapProcessorTask.initialize();
        _pageMenu = pageMenu;
        _sitemapContainer = sitemapContainer;
    }
    
    // Delete the unused menu items
    public function invoke() as Void {
        // The number of used menu items equals to the number
        // of sitemap elements. So number of elements equals
        // to the first UNUSED index.
        var i = _sitemapContainer.getWidgets().size();
        // Logger.debug( "DeleteUnusedMenuItemsTask.invoke: deleting menu item, starting from index " + i );

        // NOTE: getItemCount() receives special handling in `HomepageMenu`
        // for touch-based devices. It effectively hides the settings menu entry
        // by returning the item count excluding it, in order to prevent the
        // entry from being deleted as an unused menu item.
        while( i < _pageMenu.getItemCount() ) {
            var menuItem = _pageMenu.getItem( i ) as CustomMenuItem;
            if( menuItem instanceof ContainerMenuItem ) {
                _pageMenu.invalidateStructure();
                // Logger.debug( "DeleteUnusedMenuItemsTask.update: page '" + _pageMenu.getTitle() + "' invalid because subpage was removed" );
            }
            _pageMenu.deleteItem( i );
        }
    }
}


/*
 * Due to a bug in Garmin's native implementation of CustomMenu/Menu2,
 * adding or removing items does not properly update the currently displayed menu.
 * As a workaround, the view must be reloaded manually.
 * See the SwitchViewIfVisibleTask base class for further details.
 */
class SwitchViewIfVisibleAndItemCountChangedTask extends SwitchViewIfVisibleTask {
    private var _previousItemCount as Number;

    // Constructor
    // The task needs to be created before any modification
    // of the BasePageMenu. It will record the current number
    // of items, and compare that with the number of items on
    // invokation.
    public function initialize( 
        pageMenu as BasePageMenu 
    ) {
        SwitchViewIfVisibleTask.initialize( pageMenu );
        _previousItemCount = pageMenu.getItemCount();
    }
    
    // Perform the switch if needed
    public function invoke() as Void {
        // Logger.debug( "SwitchViewIfVisibleAndItemCountChangedTask.invoke" );
        // Replacing items works as expected, so only when 
        // the item count has changed ...
        if( _previousItemCount != getPageMenu().getItemCount() ) {
            // ... we invoke the base class, which checks if the menu
            // is visible and if yes switches it.
            SwitchViewIfVisibleTask.invoke();
        }
    }
}
