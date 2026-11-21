import Toybox.Lang;

/*
 * Task for asynchronously creating a sitemap element and
 * adding it to the parent container.
 *
 * An index of -1 appends the element, as used when a container is created.
 * A non-negative index replaces the element at that position, as used for
 * partial sitemap updates.
 */
class SitemapContainerBuildTask extends BaseSitemapProcessorTask {
    private var _sitemapContainer as SitemapContainer;
    private var _widget as JsonAdapter;
    private var _weakTaskQueue as WeakReference;
    private var _index as Number;

    // Constructor
    // Storing all the data we need for adding or replacing the element
    public function initialize( 
        sitemapContainer as SitemapContainer, 
        widget as JsonAdapter,
        taskQueue as TaskQueue,
        index as Number
    ) {
        BaseSitemapProcessorTask.initialize();
        _sitemapContainer = sitemapContainer;
        _widget = widget;
        _weakTaskQueue = taskQueue.weak();
        _index = index;
    }
    
    // Add or replace the element
    public function invoke() as Void {
        var taskQueue = _weakTaskQueue.get() as TaskQueue?;
        if( taskQueue == null ) {
            throw new GeneralException( "TaskQueue reference is no longer valid" );
        }

        var sitemapWidget = SitemapWidgetFactory.createByType( 
            _widget,
            _sitemapContainer.isSitemapFresh(),
            taskQueue
        );

        if( _index < 0 ) {
            _sitemapContainer.addWidget( sitemapWidget );
        } else {
            _sitemapContainer.replaceWidget( _index, sitemapWidget );
        }
    }
}