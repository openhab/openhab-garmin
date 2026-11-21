import Toybox.Lang;
import Toybox.WatchUi;

/*
 * This class represents container elements within the sitemap,
 * such as the homepage, other pages and frame elements. Containers
 * can hold multiple widgets.
 *
 * It supports two modes for creating widget elements:
 * - Synchronous: used when no menu is currently displayed and speed is important
 * - Asynchronous: used when a menu is already displayed and UI responsiveness is a priority
 *
 * These two modes are necessary because accessing the dictionaries
 * in which the Garmin SDK packs the JSON data is relatively slow.
 * Processing large structures synchronously can block the UI and
 * lead to noticeable delays in user interaction.
 */

// This type can be used as generic type for all implementations
// This is needed for accessing members that are not provided by
// SitemapContainer, but still are common to all implementations (=title)
typedef SitemapContainerImplementation as 
    SitemapPage or 
    SitemapFramePage;

class SitemapContainer extends SitemapElement {

    // The elements of this page
    private var _widgets as Array<SitemapWidget> = new Array<SitemapWidget>[0];

    // True if the received JSON contained at least one child element, or if a
    // derived widget (e.g. the Group control entry) was added.
    //
    // This is known synchronously when the container is created, in contrast to
    // `_widgets`, which is populated asynchronously by the build tasks. It is
    // therefore the reliable way to distinguish a page without content from a
    // page whose content has not been built yet.
    private var _hasWidgets as Boolean = false;

    // Constructor
    public function initialize( 
        json as JsonAdapter, 
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) {
        SitemapElement.initialize( isSitemapFresh );

        // An empty `widgets` array is valid for nested pages. openHAB returns
        // it for a Frame without children, and for containers whose children
        // are all hidden by visibility rules. SitemapHomepage validates that
        // the homepage itself is not empty.
        var jsonWidgets = json.getOptionalArray( "widgets" );
        if( jsonWidgets == null ) {
            jsonWidgets = new JsonAdapterArray[0];
        } else {
            _hasWidgets = true;
        }

        // We queue tasks at the FRONT of the queue because they must be processed
        // before previously scheduled tasks (e.g., UI updates already scheduled by
        // `SitemapApplier`).
        // Since each task is added to the front, the order of execution is reversed —
        // the last widget added will be processed first. Therefore, we add widgets
        // in reverse order: starting with the last and ending with the first.
        for( var i = jsonWidgets.size() - 1; i >= 0; i-- ) {
            var jsonWidget = jsonWidgets[i];
            taskQueue.addToFront( 
                new SitemapContainerBuildTask(
                    self,
                    jsonWidget,
                    taskQueue
                )
            );
        }
    }

    // Adds a widget, used by the build task. Also used for the derived Group
    // control entry, which is why it updates the emptiness flag.
    public function addWidget( widget as SitemapWidget ) as Void {
        _widgets.add( widget );
        _hasWidgets = true;
    }

    // Returns true if this page has content according to the received JSON, or
    // if a derived widget was added, or if widgets have already been built.
    // Returns false for a page that is empty.
    //
    // Do not use getWidgets().size() alone for this purpose: the widgets are
    // created asynchronously, so an empty array may only mean that the page is
    // still being built.
    public function hasWidgets() as Boolean {
        return _hasWidgets || _widgets.size() > 0;
    }

    // Returns the elements of this page
    public function getWidgets() as Array<SitemapWidget> {
        return _widgets;
    }
}