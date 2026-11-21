import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;
import Toybox.Time;

/*
 * Abstract base class for menu items that display sitemap widgets.
 *
 * This class maps widget-specific properties from the given `SitemapWidget` to the menu item
 * and shares some configuration with its base class, `StructuredMenuItem`.
 *
 * In addition to this property mapping, it implements support for nested sitemap elements,
 * such as frames or groups, by enabling submenu navigation.
 *
 * To support submenu functionality, subclasses should override `onSelect()` and call this
 * base class implementation. If `onSelect()` returns `true`, the event was handled by this
 * class (e.g., opening a submenu). If it returns `false`, the subclass can proceed with
 * its own handling of the select event.
 */

// Defines the options accepted by the `WidgetMenuItem` class.
typedef BaseWidgetMenuItemOptions as {
    :sitemapWidget as SitemapWidget,
    :stateTextResponsive as String?,
    :stateDrawable as StructuredMenuItem.StateDrawable?,
    :isActionable as Boolean?, // if true, the action icon is displayed
    :parent as BasePageMenu,
    :processingMode as BasePageMenu.ProcessingMode
};

class WidgetMenuItem extends StructuredMenuItem {

    // Store whether the subclass indicated that an action icon should be shown
    // This information is passed into the constructor as option, but needed 
    // also during updates
    private var _isActionable as Boolean;

    // The submenu representing the nested elements, if there are any
    private var _page as PageMenu?;

    // The current sitemap widget associated with this menu item
    private var _sitemapWidget as SitemapWidget;

    // Reference to the parent menu is required so that submenus
    // can navigate back. Submenus may be created not only in the
    // constructor, but also dynamically in updateWidget(), so we
    // store the parent menu as a member variable and use a weak
    // reference to avoid memory leaks due to circular references.
    private var _weakParent as WeakReference;

    // Constructor
    protected function initialize( 
        options as BaseWidgetMenuItemOptions
    ) {
        // Store the parent
        _weakParent = ( options[:parent] as BasePageMenu ).weak();

        var isActionable = options[:isActionable] as Boolean?;
        _isActionable = isActionable != null && isActionable;

        _sitemapWidget = options[:sitemapWidget] as SitemapWidget;

        // And initialize the base class, partly with data from
        // the SitemapWidget, partly with other options
        StructuredMenuItem.initialize( {
            :label => _sitemapWidget.getLabel(),
            :stateDrawable => options[:stateDrawable],
            :stateTextResponsive => options[:stateTextResponsive]
        } );

        processWidget( 
            _sitemapWidget,
            options[:processingMode] as BasePageMenu.ProcessingMode
        );
    }


    // Returns the current sitemap widget
    protected function getSitemapWidget() as SitemapWidget {
        return _sitemapWidget;
    }


    // Returns true if the widget is linked to a page (sub menu)
    public function hasPage() as Boolean {
        return _page != null;
    }
    

    /*
     * Subclasses can to override this method to process state changes.
     * Note: this is triggered only when the state has changed. If updates
     * to other widget data elements should be processed independent of a
     * state change, then updateWidget() should be overriden to process those.
     */
    public function onStateChanged() as Void {}


    // Handles selection of the menu item.
    //
    // If a submenu is present, it is opened on selection. This typically takes precedence
    // over changing the state of the item. Therefore, subclasses should first call the
    // parent class’s onSelect() method. If it returns false (i.e., the event was not handled),
    // the subclass can proceed with its own selection logic (e.g., state changes).
    //
    // This function also ensures that a connection is available before sending a command.
    // If no connection is present, it attempts to open the required submenus. If no
    // submenu exists to establish a connection, it throws an OfflineCommandException.
    //
    // When the OfflineCommandException is passed to
    // ExceptionHandler.handleUserInterfaceException by the delegate, it is shown to the 
    // user as a toast notification.
    //
    // Subclasses without custom selection behavior, such as ContainerMenuItem or TextMenuItem,
    // do not need to override this method.
    public function onSelect() as Boolean { 
        var linkedPage = _sitemapWidget.getLinkedPage();
        if( linkedPage != null ) {
            if( ! linkedPage.hasWidgets() ) {
                // Pushing an empty CustomMenu leads to rendering issues. Pages
                // that have content but whose menu items are still being created
                // keep the existing LoadingMenuItem behavior below.
                WarningToastHandler.showInfo( Rez.Strings.EmptyPage );
                return true;
            }

            if( _page == null ) {
                // Defensive fallback: normally processWidget() creates the page
                // menu, but do not lose an item if it was not created.
                var parent = _weakParent.get() as BasePageMenu?;
                if( parent != null ) {
                    _page = new PageMenu(
                        linkedPage as SitemapContainerImplementation,
                        parent,
                        BasePageMenu.PROCESSING_ASYNC
                    );
                }
            }
        }

        if( _page != null ) {
            var page = _page;
            // Logger.debug( "WidgetMenuItem.onSelect: pushing page" );
            // Pushing a CustomMenu with no menu items leads to weird behavior.
            // Therefore ensureItems() adds a LoadingMenuItem if no other
            // items are present at this time. See ensureItems() for details.
            page.ensureItems();
            ViewStack.pushView( page, PageMenuDelegate.get(), WatchUi.SLIDE_LEFT );
            return true;
        } else {
            if( ! ConnectionManager.get().isConnected() ) {
                throw new OfflineCommandException();
            }
            return false;
        }
    }


    /*
     * Internal function used to process data both during initialization and
     * when processing an update.
     */
    private function processWidget( 
        sitemapWidget as SitemapWidget,
        processingMode as BasePageMenu.ProcessingMode
    ) as Void { 
        setIcon( sitemapWidget.getIcon() );
        setLabel( sitemapWidget.getLabel() );
        setLabelColor( sitemapWidget.getLabelColor() );
        setStateColor( sitemapWidget.getValueColor() );

        var linkedPage = sitemapWidget.getLinkedPage() as SitemapContainerImplementation?;
        if( linkedPage != null ) {
            setActionIcon( ACTION_ICON_PAGE );
            if( _page != null ) {
                _page.update( linkedPage );
            } else {
                var parent = _weakParent.get() as BasePageMenu?;
                if( parent == null ) {
                    throw new GeneralException( "Parent reference is no longer valid" );
                }
                _page = new PageMenu( linkedPage, parent, processingMode );
            }
        } else {
            // The widget no longer has a linked page. Drop any previously
            // created PageMenu so that a stale submenu cannot be opened
            // after an update, and close a page that is currently displayed.
            if( _page != null ) {
                var parent = _weakParent.get() as BasePageMenu?;
                if( parent != null ) {
                    parent.invalidateStructure();
                }
            }
            _page = null;
            if( _isActionable ) {
                setActionIcon( ACTION_ICON_COMMAND );
            } else {
                setActionIcon( null );
            }
        }
    }


    /*
     * Updates the menu item with new data received from the server.
     * The provided `SitemapWidget` contains the updated information.
     *
     * Subclasses may override this method if they need to process additional
     * parts of the update, but they must also call the base class’s
     * updateWidget() to ensure core functionality is preserved.
     *
     * To process state changes subclasses SHOULD NOT use this function, but
     * instead override onStateChanged(). This method is only called
     * if the state has changed, subclasses may use that implementation also
     * to process local changes.
     */
    public function updateWidget( sitemapWidget as SitemapWidget ) as Void { 
        // Store the new widget instance
        var previousSitemapWidget = _sitemapWidget;
        _sitemapWidget = sitemapWidget;

        // Update the label and formatting in the base class
        // during an update, we always use the async task queue
        processWidget( sitemapWidget, BasePageMenu.PROCESSING_ASYNC );

        // Determine if the display state or state as changed and if
        // yes, call onStateChanged()
        var previousItem = previousSitemapWidget.getItem();
        var newItem = sitemapWidget.getItem();
        var hasStateChanged =
            ! previousSitemapWidget.getDisplayState().equals( sitemapWidget.getDisplayState() )
            || ( previousItem == null ) != ( newItem == null ) 
            || ( previousItem != null 
                 && newItem != null
                 && ! previousItem.getState().equals( newItem.getState() ) );

        if( hasStateChanged ) {
            onStateChanged();
        }

        WatchUi.requestUpdate();
    }
}