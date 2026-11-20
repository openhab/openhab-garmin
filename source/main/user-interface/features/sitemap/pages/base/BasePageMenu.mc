import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.System;
import Toybox.Graphics;

/*
 * Base class for page menus.
 *
 * Page menus serve as containers for primitive sitemap elements. These containers 
 * correspond to elements such as the Homepage and Frames, while primitive elements 
 * include Switches, Text elements, and similar controls.
 *
 * This class is responsible for both creating menu items based on the sitemap and 
 * updating the menu in response to changes in the sitemap.
 *
 * Because Connect IQ apps are single-threaded and processing can be time-consuming, 
 * the logic is divided into multiple tasks. These tasks are managed by an 
 * AsyncTaskQueue, allowing brief pauses between them so that user input can be 
 * processed. This approach helps prevent noticeable lag or UI unresponsiveness 
 * when dealing with large sitemaps.
 *
 * Updates are always handled fully asynchronously. Initialization, however, can 
 * be performed either in a synchronous/asynchronous hybrid mode or fully 
 * asynchronously. In the hybrid mode, the top-level menu is initialized 
 * synchronously for immediate display, while lower-level elements are populated 
 * asynchronously.
 */
class BasePageMenu extends BaseMenu {
    
    // Config used to identify the initialization processing mode:
    //
    // PROCESSING_TOP_LEVEL_SYNC:
    //   Indicates that this menu level should be processed synchronously,
    //   while subsequent levels will be processed asynchronously.
    //
    // PROCESSING_ASYNC_AFTER_SYNC:
    //   Marks the first menu level that is populated asynchronously after a
    //   synchronous top-level. This case requires special handling because
    //   the user may already have opened the (initially empty) menu that was
    //   created synchronously. A workaround for a Garmin bug affecting visible
    //   menus must be applied, along with a UI update.
    //
    // PROCESSING_ASYNC:
    //   Indicates full asynchronous processing. Used for lower levels in hybrid mode
    //   (second level and below), but can also be applied to the root (e.g., HomepageMenu)
    //   if full asynchronous initialization is desired.
    public enum ProcessingMode {
        PROCESSING_TOP_LEVEL_SYNC,
        PROCESSING_ASYNC_AFTER_SYNC,
        PROCESSING_ASYNC
    }

    // The label for the menu
    private var _title as String;

    // Constructor
    // Initializes the menu and adds menu items for each widget in the
    // provided sitemapContainer.
    // See above for details on the processingMode parameter.
    protected function initialize( 
        sitemapContainer as SitemapContainerImplementation, 
        footer as Drawable?,
        processingMode as ProcessingMode 
    ) {
        _title = sitemapContainer.title;
        
        // Initialize the base class
        BaseMenu.initialize( {
                :title => _title,
                :itemHeight => Config.UI_MENU_ITEM_HEIGHT,
                :footer => footer
            } );

        var widgets = sitemapContainer.getWidgets();

        // For all modes, we first obtain the task queue.
        var taskQueue = AsyncTaskQueue.get();

        // Create a menu item for each widget.
        if( processingMode == PROCESSING_TOP_LEVEL_SYNC ) {
            // Logger.debug( "BasePageMenu.initialize: synchronous processing" );
            // In top-level sync mode, menu items are created synchronously.
            // The next level is then initialized in PROCESSING_ASYNC_AFTER_SYNC mode.
            for( var i = 0; i < widgets.size(); i++ ) {
                addItem( MenuItemFactory.createMenuItem( widgets[i], self, PROCESSING_ASYNC_AFTER_SYNC ) );
            }
            // Since now we are showing the UI, we prioritize responsiveness over
            // speed
            taskQueue.prioritizeResponsiveness();
        } else {
            if( processingMode == PROCESSING_ASYNC_AFTER_SYNC ) {
                // If we are the first async level, we schedule the tasks
                // needed to update the UI if the user has already navigated
                // to this level.
                // Logger.debug( "BasePageMenu.initialize: first asynchronous processing" );
                
                // Not needed as long as SwitchViewIfVisibleTask is required,
                // which remains the case until Garmin fixes the related bug.
                // SwitchViewIfVisibleTask implicitly refreshes the UI, so an explicit
                // refresh is not necessary.
                // See SwitchViewIfVisibleTask for details.
                // taskQueue.addToFront( new RequestWatchUiUpdateIfVisibleTask( self ) );
                
                taskQueue.addToFront( new SwitchViewIfVisibleTask( self ) );
            } else {
                // Logger.debug( "BasePageMenu.initialize: asynchronous processing" );
            }

            // Now add the tasks for creating the menu items. Since we schedule
            // them to the front of the queue they'll be executed BEFORE the UI
            // update tasks above.
            for( var i = widgets.size() - 1; i >= 0; i-- ) {
                taskQueue.addToFront( new CreateMenuItemTask( widgets[i], self ) );
            }
        }
    }

    // The menu structure remains valid after an update as long as
    // no pages are added or removed anywhere in the tree.
    //
    // If the structure becomes invalid (e.g., due to page additions or deletions),
    // the view is reset to the Homepage. This ensures we don't remain in a submenu
    // that may have been deleted or changed to a different menu item type.
    //
    // This function is implemented differently by HomepageMenu and PageMenu:
    // - HomepageMenu stores a Boolean flag indicating if the structure is still valid.
    // - PageMenu delegates this check to its parent.
    public function invalidateStructure() as Void {
        throw new AbstractMethodException( "BasePageMenu.invalidateStructure" );
    }

    // Returns the menu title
    public function getTitle() as String {
        return _title;
    }

    /*
    * Updates the menu based on the current sitemap state.
    *
    * This algorithm uses positional indexing based on the order of elements in the sitemap JSON.
    * When a new element is inserted mid-page, it receives the identifier previously assigned
    * to the item at that position, and all subsequent items are reindexed accordingly.
    *
    * For each sitemap element, the algorithm compares it to the existing menu item at the same index:
    *   - If types match, the item is updated in place.
    *   - If types differ, the existing item is replaced.
    *   - If no menu item exists at that index, a new one is added.
    *   - Any remaining items beyond the end of the new sitemap list are removed.
    *
    * This enables efficient reuse of menu items when elements are added, removed, or reordered— 
    * as long as the types are compatible. For example, inserting a `Switch` in the middle of a list 
    * of `Switch` items will repurpose the existing item at that position and adjust the rest accordingly.
    *
    * The `update()` function also tracks whether the menu structure was affected.
    * See `invalidateStructure()` for details.
    *
    * NOTE: This algorithm does *not* use the `widgetId`. While `widgetId` often reflects the 
    * position of an element, this breaks down when the `visibility` parameter is used—hidden items 
    * retain their `widgetId`, creating "holes" in the sequence. To avoid this, we rely on actual 
    * array indices instead.
    */
    public function update( sitemapContainer as SitemapContainerImplementation ) as Void {
        updateWithQueue( sitemapContainer, AsyncTaskQueue.get() );
    }

    /*
    * Same as update(), but the tasks are queued in the provided queue instead
    * of the AsyncTaskQueue.
    *
    * A SyncTaskQueue is used when the update must be visible with the next
    * frame, e.g. when hiding expired states before the app is drawn again
    * after it was hidden by the system. The caller is responsible for
    * executing the tasks, see HomepageMenu.hideStates().
    *
    * The algorithm described above is not affected by the queue type.
    */
    protected function updateWithQueue( 
        sitemapContainer as SitemapContainerImplementation,
        taskQueue as TaskQueue
    ) as Void {
        // Logger.debug( "PageMenu.update: updating page '" + _title + "'" );

        // Update the title of the menu
        _title = sitemapContainer.title;
        setTitleAsString( _title );

        // Since the `SitemapProcessor` has already added tasks that should run
        // AFTER the menu update, we prepend all tasks to the queue.
        // As a result, the tasks here are added in REVERSE order of their
        // intended execution. For example, we add the `SwitchViewIfVisibleAndItemCountChangedTask`
        // first, because it is meant to be executed last.

        // Workaround for a bug in Garmin's native device implementation 
        // of the CustomMenu/Menu2. See `SwitchViewIfVisibleAndItemCountChangedTask`
        // for details. This task needs to be created before any modifications
        // to the menu structure, because on initialization it needs to save the
        // current number of menu items.
        taskQueue.addToFront( new SwitchViewIfVisibleAndItemCountChangedTask( self ) );

        // After we have cycled through all sitemap elements, 
        // we'll delete any menu items that are not used anymore
        taskQueue.addToFront( 
            new DeleteUnusedMenuItemsTask( 
                sitemapContainer as SitemapContainer, 
                self 
            ) 
        );

        // Loop through all elements in the new sitemap state
        // and add a task for each to process them.
        // We do this in reverse order so that the tasks are
        // executed in the same order as the corresponding sitemap elements.
        var widgets = sitemapContainer.getWidgets();
        for( var i = widgets.size() - 1; i >= 0; i-- ) {
            var widget = widgets[i];
            taskQueue.addToFront( new AddOrUpdateMenuItemTask( i, widget, self ) );
        }
    }
}