import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.System;

/*
 * `HomepageMenu` represents the root of the sitemap and is implemented as a singleton,
 * as there can only be one root.
 *
 * It behaves similarly to a standard `PageMenu`, but includes additional functionality
 * for accessing the settings menu.
 *
 * - On button-based devices: the settings menu is accessed by scrolling past the
 *   header or footer of the menu (i.e., it's a parallel menu).
 * - On touch-based devices: the settings menu is accessed via a dedicated menu item.
 *
 * Some functions in this class are specific to either button-based or touch-based devices,
 * and are conditionally excluded from compilation for the other device type.
 */
class HomepageMenu extends BasePageMenu {
    

    /******* STATIC *******/


    // The Singleton instance
    private static var _instance as HomepageMenu?;
    
    // Completely removes the current menu structure.
    // Used to start fresh after out-of-memory errors,
    // which can occur if old and new sitemaps differ greatly
    // and their menu structures partially coexist during updates.
    public static function clear() as Void {
        _instance = null;
    }

    // Creates the menu from a sitemap received via a web response.
    // 
    // This function uses an asynchronous task queue to process the entire sitemap
    // instead of recursive function calls. Garmin CIQ apps have a relatively small
    // maximum stack size, so this approach helps prevent stack overflow when handling
    // deeply nested sitemaps.
    //
    // Additionally, the asynchronous task queue processes tasks in small batches,
    // allowing user input to be handled between batches to improve UI responsiveness.
    public static function createFromWebResponse( sitemapHomepage as SitemapHomepage ) as HomepageMenu {
        _instance = new HomepageMenu( sitemapHomepage, false );
        return _instance;
    }
    
    // Used on startup by OHApp to initialize the menu from a stored sitemap,
    // if one is available. While UI responsiveness is not a concern during startup,
    // the Watchdog still enforces a limit on consecutive code execution time.
    //
    // Large sitemaps, especially those with many unique icons, may exceed this limit.
    // To avoid this, only the first level is processed synchronously,
    // allowing the menu to open immediately. The remaining levels are populated
    // asynchronously after startup.
    public static function createFromStorage() as HomepageMenu? {
        // Reading the JSON from storage is not critical,
        // if it fails, we just show the LoadingView and wait
        // for the first response from the server to arrive
        try {
            var sitemapHomepage = SitemapStore.getSitemapFromStorage();
            if( sitemapHomepage != null ) {
                _instance = new HomepageMenu( sitemapHomepage, true );
            }
        } catch( ex ) {
            Logger.debugException( ex );
        }
        return _instance;
    }

    // True if the menu was already created
    public static function exists() as Boolean {
        return _instance != null;
    }

    // Returns the menu, or throws an exception if
    // the menu was not created yet
    public static function get() as HomepageMenu {
        if( _instance == null ) {
            throw new GeneralException( "HomepageMenu: call create() before get()" );
        }
        return _instance as HomepageMenu;
    }

    // Hides the states by retrieving the cached sitemap, marking all states as
    // stale, and updating the menu.
    //
    // The menu is updated synchronously: when the app becomes visible again
    // after it was hidden by the system, the invalidated states must be part
    // of the first frame. An asynchronous update would show the previous
    // states until the task queue has worked through the menu.
    //
    // The states of the rebuilt model no longer match the stored JSON, so the
    // divergence is recorded here: without it, an incoming response with an
    // unchanged JSON would be skipped and the states would never come back.
    //
    // Returns true if states were hidden. The menu can exist without a sitemap
    // in memory, e.g. after a fatal error cleared it, so this is a no-op then.
    public function hideStates() as Boolean {
        var sitemap = SitemapStore.getSitemapFromMemoryWithStaleStates();
        if( sitemap == null ) {
            return false;
        }

        var taskQueue = new SyncTaskQueue();
        updateWithQueue( sitemap, taskQueue );
        taskQueue.executeTasks();

        SitemapStore.noteModelDivergence();
        WatchUi.requestUpdate();

        return true;
    }

    // Hides the states if the sitemap has expired. Used when the app becomes
    // visible again after it was hidden by the system: the states captured
    // before are shown only if they are still within the expiration time.
    public function hideStatesIfStale() as Boolean {
        if( SitemapStore.isSitemapFresh() ) {
            return false;
        }
        return hideStates();
    }
    
    // Returns true, if any sitemap-related view is currently showing
    // These are:
    // - Any `CustomMenu` implementations, exception the `SettingsMenu`
    // - Any `ControlView`
    public static function isSitemapShowing() as Boolean {
        var currentView = ViewStack.getCurrentView()[0];
        return  ( currentView instanceof CustomMenu && ! ( currentView instanceof SettingsMenu ) )
                || currentView instanceof ControlView;
    }

    /******* INSTANCE *******/


    // See BasePageMenu.invalidateStructure for details
    private var _structureRemainsValid as Boolean = true;
    
    // See BasePageMenu.invalidateStructure for details
    public function invalidateStructure() as Void {
        _structureRemainsValid = false;
    }

    // The HomepageMenu is the entry point into the
    // menu structure, where we want to display toast notifications for
    // non-fatal errors
    public function onShow() as Void {
        WarningToastHandler.setUseToasts( true );
    }

    // See BasePageMenu.invalidateStructure for details
    public function structureRemainsValid() as Boolean {
        return _structureRemainsValid;
    }

    // Before an update we reset the indicator showing whether
    // the update left the structure intact.
    // Then we call the update function of the base class
    public function update( sitemapContainer as SitemapContainerImplementation ) as Void {
        _structureRemainsValid = true;
        BasePageMenu.update( sitemapContainer );
    }

    /******* BUTTON-BASED DEVICES *******/

    // Constructor for button-based devices. On these devices,
    // the settings icon is displayed in the footer.
    // The syncTopLevel parameter indicates that the HomepageMenu
    // should add its menu items synchronously, with only the lower levels
    // initialized asynchronously.
    (:exclForSettingsMenuItem)
    private function initialize(
        sitemapHomepage as SitemapHomepage,
        syncTopLevel as Boolean
    ) {
        BasePageMenu.initialize( 
            sitemapHomepage, 
            new Bitmap( {
                    :rezId => Rez.Drawables.iconDownToSettings,
                    :locX => WatchUi.LAYOUT_HALIGN_CENTER,
                    :locY => WatchUi.LAYOUT_VALIGN_CENTER } ),
            syncTopLevel
                ? PROCESSING_TOP_LEVEL_SYNC
                : PROCESSING_ASYNC
        );
    }


    /******* TOUCH-BASED DEVICES *******/


    // For touch-based devices there is a dedicated menu item for
    // showing the settings menu
    // This member tracks whether the settings menu has been added
    (:exclForSettingsFooter)
    private var _hasSettingsMenu as Boolean = false;

    // Constructor for touched-based devices. On these devices,
    // there is a dedicated settings menu item added to the HomepageMenu.
    // The syncTopLevel parameter indicates that the HomepageMenu
    // should add its menu items synchronously, with only the lower levels
    // initialized asynchronously.
    (:exclForSettingsFooter)
    private function initialize(
        sitemapHomepage as SitemapHomepage,
        syncTopLevel as Boolean
    ) {
        BasePageMenu.initialize( 
            sitemapHomepage, 
            null, 
            syncTopLevel
                ? PROCESSING_TOP_LEVEL_SYNC
                : PROCESSING_ASYNC 
        );
        BasePageMenu.addItem( SettingsMenuItem.get() );
        _hasSettingsMenu = true;
    }

    // The update function syncs the menu structure with the sitemap
    // The following two functions are overriden to "hide" the last
    // entry, the settings menu item, from the update procedure
    // We pretent to have one menu item less ...
    (:exclForSettingsFooter) 
    public function getItemCount() as Number {
        return 
            _hasSettingsMenu
            ? BasePageMenu.getItemCount() - 1
            : BasePageMenu.getItemCount();
    }
    // And if a menu item is added, we first remove the settings,
    // then add the new menu item, and then add the settings again
    // in the end
    (:exclForSettingsFooter) 
    public function addItem( menuItem as CustomMenuItem ) as Void {
        var accountForSettingsMenu = _hasSettingsMenu;
        if( accountForSettingsMenu ) {
            BasePageMenu.deleteItem( BasePageMenu.getItemCount() - 1 );
            _hasSettingsMenu = false;
        }
        BasePageMenu.addItem( menuItem );
        if( accountForSettingsMenu ) {
            BasePageMenu.addItem( SettingsMenuItem.get() );
            _hasSettingsMenu = true;
        }
    }
}