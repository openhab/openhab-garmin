import Toybox.Lang;

/*
 * This class represents the root element of the sitemap (Homepage).
 */
class SitemapHomepage extends SitemapPage {

    /**
    * Constructor
    *
    * @param json - The root JSON object from openHAB.
    * @param isSitemapFresh - Indicates whether the sitemap is fresh (i.e., not older than the expiry time).
    * @param asyncProcessing - If true, asynchronous processing is used to avoid blocking the UI during sitemap updates.
    *                          In this case, tasks are added to the AsyncTaskQueue. Any logic that depends on the
    *                          sitemap being fully processed must be scheduled afterward.
    *                          To ensure this, sitemap-related tasks are always added to the *front* of the queue.
    *                          If false, a SyncTaskQueue is used instead, which avoids stack overflow issues
    *                          while allowing immediate execution.
    */
    public function initialize( 
        json as JsonAdapter, 
        isSitemapFresh as Boolean,
        asyncProcessing as Boolean 
    ) {
        var taskQueue = 
            asyncProcessing
            ? AsyncTaskQueue.get()
            : new SyncTaskQueue();

        // We extract the homepage element and pass 
        // it on to the base class. A homepage without widgets is not useful
        // and is therefore still treated as an error; nested pages may be
        // empty and are handled by the UI.
        var homepageJson = json.getObject( "homepage", "Homepage: homepage element missing" );
        if( homepageJson.getOptionalArray( "widgets" ) == null ) {
            throw new JsonParsingException( "Homepage contains no elements" );
        }
        SitemapPage.initialize( 
            homepageJson,
            isSitemapFresh,
            taskQueue
        );

        // The base class always creates tasks to process its elements
        // If synchronous processing has been requested, we execute
        // those tasks immediately.
        // Processing the elements sequentially instead of recursively
        // is needed to avoid stack overflow withs larger sitemaps
        if( taskQueue instanceof SyncTaskQueue ) {
            taskQueue.executeTasks();
        }
    }

    // Updates the existing homepage model for a partial sitemap update.
    // Only the top level elements at the given indices are rebuilt; the
    // remaining widgets are reused as-is. Nested pages of the rebuilt widgets
    // are created by the usual task mechanism.
    public function updateFromJson(
        json as JsonAdapter,
        changedIndices as Array<Number>,
        taskQueue as TaskQueue
    ) as Void {
        var homepageJson = json.getObject( 
            "homepage", 
            "Homepage: homepage element missing" 
        );
        var jsonWidgets = homepageJson.getOptionalArray( "widgets" );
        if( jsonWidgets == null ) {
            throw new JsonParsingException( "Homepage contains no elements" );
        }

        title = parseLabelState( 
            homepageJson, 
            "title", 
            "Page title is missing" 
        )[0];

        updateWidgets( jsonWidgets, changedIndices, taskQueue );
    }
}