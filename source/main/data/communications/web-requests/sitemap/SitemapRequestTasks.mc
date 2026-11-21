import Toybox.Lang;

/*
 * This file defines all asynchronous task classes used to update an existing
 * menu structure based on responses from `SitemapRequest`.
 *
 * NOTE: Many of these tasks are time-consuming and will therefore schedule
 * additional asynchronous tasks to the *front of the queue*, ensuring they
 * are executed before the next queued task.
 *
 * The response is processed using the following task sequence:
 *
 * 1) ProcessIncomingJsonTask:
 *    Triggered by `SitemapRequest` with a JSON dictionary as input.
 *    It creates a `SitemapHomepage` and schedules the next task in the sequence.
 *
 *    If the menu already displays the stored JSON (i.e. nothing has diverged,
 *    see SitemapStore.noteModelDivergence()), the response is compared with the
 *    stored one first. The comparison is split over several tasks:
 *      1.0) CompareSitemapTask:
 *           Compares the root of both sitemaps and schedules one
 *           CompareSitemapElementTask per top level element, followed by
 *           FinishSitemapComparisonTask.
 *
 *    If the response is unchanged, it is dropped and the next request is
 *    triggered. Otherwise the menu is created or updated as described below.
 *
 *    If only top level elements changed and the displayed model is complete
 *    and in sync, only those elements and their menu items are rebuilt
 *    (see SitemapApplier.applyPartial). If a partial update is not safe, the
 *    full path described below is used.
 *
 *    If no menu exists yet, this task initiates:
 *      1.1) CreateHomepageTask:
 *           Initializes the menu structure using asynchronous tasks.
 *      1.2) SwitchToHomepageTask:
 *           Switches from the current view to the `HomepageMenu`.
 *
 *    If a menu already exists, it initiates:
 *      1.3) UpdateHomepageTask:
 *           Updates the existing menu structure asynchronously.
 *      1.3.1) UpdateHomepageIndicesTask:
 *           Updates only the menu items of changed top level elements for a
 *           partial update.
 *      1.4) RefreshUiTask:
 *           Refreshes the UI based on the current state:
 *           - If in a valid menu, refreshes the current screen.
 *           - If the menu structure is invalid or an error view is active, 
 *             navigates to the homepage menu.
 *           - If in the settings menu, does nothing.
 *
 * 2) MakeNextRequestTask:
 *    Triggered at the end of both the above sequences (from either
 *    SwitchToHomepageTask or RefreshUiTask) to initiate the next request.
 */

// Base class for asynchronous tasks that implement the
// processing of menu initialization and update
class BaseSitemapProcessorTask {
    protected function initialize() {
    }
    public function handleException( ex as Exception ) as Void {
        // Note: SitemapRequest.handleException differentiates between
        // normal mode and sync mode, and in the case of the latter
        // passes the exception on to the sync delegate
        SitemapRequest.get().handleException( ex );
    }
}

// 1)
// The first task initalizes with the incoming JSON
// and instantiates a SitemapHomepage to represent it.
// The hierarchy is processed iteratively: SitemapPage, the base
// class of SitemapHomepage, creates tasks on its own for the processing.
// These tasks are added to THE FRONT OF THE QUEUE, and therefore will be
// executed before the next step is triggered.
class ProcessIncomingJsonTask extends BaseSitemapProcessorTask {

    private var _json as SitemapJsonIncoming;
    public function initialize( json as SitemapJsonIncoming ) {
        BaseSitemapProcessorTask.initialize();
        _json = json;
    }

    public function invoke() as Void {
        // Logger.debug( "ProcessIncomingJsonTask.invoke" );

        var isSyncInProgress = SafeSitemapSyncDelegate.isSyncInProgress();

        // In sync mode, update the progress bar
        if( isSyncInProgress ) {
            Communications.notifySyncProgress( 60 );
        }

        // An unchanged sitemap does not change anything on screen. Rebuilding
        // the data layer and the menu is the most expensive part of the
        // polling cycle, so skip it as long as the displayed model is in sync
        // with the stored JSON. See SitemapStore.noteModelDivergence().
        if( ! isSyncInProgress
            && HomepageMenu.exists()
            && SitemapStore.isModelInSync()
        ) {
            AsyncTaskQueue.get().add( new CompareSitemapTask( _json ) );
            return;
        }

        SitemapApplier.apply( _json );
    }
}

// 1.0)
// Compares the received sitemap with the stored one. The comparison is split
// into several tasks, so that no single task blocks the user interface for the
// whole sitemap: this task compares the root and schedules one task per top
// level element, followed by a task that decides what to do with the response.
class CompareSitemapTask extends BaseSitemapProcessorTask {

    private var _json as SitemapJsonIncoming;

    public function initialize( json as SitemapJsonIncoming ) {
        BaseSitemapProcessorTask.initialize();
        _json = json;
    }

    public function invoke() as Void {
        var comparison = SitemapStore.createComparison( _json.json );

        // Without a stored JSON, or if the root already differs, there is
        // nothing left to compare
        if( comparison == null || ( comparison as SitemapComparison ).compareRoot() ) {
            SitemapApplier.apply( _json );
            return;
        }

        var taskQueue = AsyncTaskQueue.get();
        var elementCount = ( comparison as SitemapComparison ).getElementCount();
        for( var i = 0; i < elementCount; i++ ) {
            taskQueue.add( new CompareSitemapElementTask( comparison, i ) );
        }
        taskQueue.add( new FinishSitemapComparisonTask( _json, comparison ) );
    }
}

// 1.0.1)
// Compares a single top level element of both sitemaps. Every element is
// evaluated because the changed indices are needed for a partial update.
class CompareSitemapElementTask extends BaseSitemapProcessorTask {

    private var _comparison as SitemapComparison;
    private var _index as Number;

    public function initialize( comparison as SitemapComparison, index as Number ) {
        BaseSitemapProcessorTask.initialize();
        _comparison = comparison;
        _index = index;
    }

    public function invoke() as Void {
        _comparison.compareElement( _index );
    }
}

// 1.0.2)
// Decides what to do with the response once the comparison is complete.
class FinishSitemapComparisonTask extends BaseSitemapProcessorTask {

    private var _json as SitemapJsonIncoming;
    private var _comparison as SitemapComparison;

    public function initialize( 
        json as SitemapJsonIncoming, 
        comparison as SitemapComparison 
    ) {
        BaseSitemapProcessorTask.initialize();
        _json = json;
        _comparison = comparison;
    }

    public function invoke() as Void {
        // The model can diverge while the comparison is running, e.g. if a
        // command is sent in the meantime. In that case the response must be
        // applied even if it is unchanged. See SitemapStore.noteModelDivergence().
        if( _comparison.isDifferent
            && canApplyPartially()
            && SitemapApplier.applyPartial( 
                _json, 
                _comparison.getChangedIndices(),
                _comparison.getElementCount()
            )
        ) {
            return;
        }

        if( _comparison.isDifferent || ! SitemapStore.isModelInSync() ) {
            SitemapApplier.apply( _json );
            return;
        }

        // The JSON is unchanged, but its age must be refreshed so that
        // the data does not expire while no update is applied.
        SitemapStore.refreshStored( _json );
        Logger.debug( "FinishSitemapComparisonTask: update dropped, sitemap unchanged" );
        AsyncTaskQueue.get().add( new TriggerNextRequestTask() );
    }

    // A partial update is only safe if the displayed model is complete and in
    // sync, and if the comparison detected no structural change.
    private function canApplyPartially() as Boolean {
        return ! _comparison.isReordered()
            && _comparison.hasChangedElements()
            && ! SafeSitemapSyncDelegate.isSyncInProgress()
            && SitemapStore.isModelInSync()
            && HomepageMenu.exists();
    }
}


// 1.1)
// Creates the HomepageMenu and then switches to it. Since initialization is
// time-consuming, HomepageMenu.create() schedules its own tasks to the
// front of the queue. The view switch is added to the back of the queue and
// will be processed only after all initialization tasks have completed.
class CreateHomepageTask extends BaseSitemapProcessorTask {
    private var _sitemapHomepage as SitemapHomepage;

    public function initialize( sitemapHomepage as SitemapHomepage ) {
        BaseSitemapProcessorTask.initialize();
        _sitemapHomepage = sitemapHomepage;
    }
    
    public function invoke() as Void {
        // Logger.debug( "CreateHomepageMenuTask.invoke" );
        
        // The whole logic fits in one statement, we
        // create the new HomepageMenu, feed it into
        // the SwitchToHomepageTask and schedule that task
        
        // var homepageMenu = HomepageMenu.createFromWebResponse( _sitemapHomepage );


        AsyncTaskQueue.get().add(
            new SwitchToHomepageTask(
                HomepageMenu.createFromWebResponse( _sitemapHomepage )
            )
        );
    }
}

// 1.2)
// In normal mode, this task switches to the newly created HomepageMenu view
// and triggers the next sitemap request.
// In sync mode, it only stops the sync. The view switch is performed after the
// sync is stopped, in PostSyncTask.
class SwitchToHomepageTask extends BaseSitemapProcessorTask {
    private var _homepageMenu as HomepageMenu;

    public function initialize( homepageMenu as HomepageMenu ) {
        BaseSitemapProcessorTask.initialize();
        _homepageMenu = homepageMenu;
    }
    
    public function invoke() as Void {
        // Logger.debug( "SwitchToHomepageTask.invoke" );
        if( SafeSitemapSyncDelegate.isSyncInProgress() ) {
            AsyncTaskQueue.get().add( new StopSitemapSyncTask() );
        } else {
            ViewStack.resetTo( 
                _homepageMenu, 
                HomepageMenuDelegate.get()
            );
            
            AsyncTaskQueue.get().add( new TriggerNextRequestTask() );
        }
    }
}


// 1.3)
// The first step for an update is to update the menu structure ...
class UpdateHomepageTask extends BaseSitemapProcessorTask {

    // This tasks needs the SitemapHomepage representing the
    // newly incoming data as input
    private var _sitemapHomepage as SitemapHomepage;
    public function initialize( sitemapHomepage as SitemapHomepage ) {
        BaseSitemapProcessorTask.initialize();
        _sitemapHomepage = sitemapHomepage;
    }

    public function invoke() as Void {

        // In sync mode, update the progress bar
        if( SafeSitemapSyncDelegate.isSyncInProgress() ) {
            Communications.notifySyncProgress( 80 );
        }

        // Logger.debug( "UpdateHomepageTask.invoke" );
        if( ! HomepageMenu.exists() ) {
            throw new GeneralException( "HomepageMenu does not exist" );
        }
        
        HomepageMenu.get().update( _sitemapHomepage );

        // If we are in sync mode we are done and stop the sync
        if( SafeSitemapSyncDelegate.isSyncInProgress() ) {
            AsyncTaskQueue.get().add( new StopSitemapSyncTask() );
        } else {
            AsyncTaskQueue.get().add( new RefreshUiTask() );
        }
    }
}

// 1.3.1)
// Updates only the menu items whose top level sitemap elements changed.
class UpdateHomepageIndicesTask extends BaseSitemapProcessorTask {

    private var _json as SitemapJsonIncoming;
    private var _sitemapHomepage as SitemapHomepage;
    private var _indices as Array<Number>;

    public function initialize( 
        json as SitemapJsonIncoming,
        sitemapHomepage as SitemapHomepage,
        indices as Array<Number>
    ) {
        BaseSitemapProcessorTask.initialize();
        _json = json;
        _sitemapHomepage = sitemapHomepage;
        _indices = indices;
    }

    public function invoke() as Void {
        if( ! HomepageMenu.exists() ) {
            // The menu was removed in the meantime (e.g. after an
            // out-of-memory error); rebuild everything.
            SitemapApplier.apply( _json );
            return;
        }

        var homepageMenu = HomepageMenu.get();
        if( ! homepageMenu.canUpdateIndices( _sitemapHomepage, _indices ) ) {
            // A structural change cannot be expressed by updating single
            // menu items; fall back to the full rebuild.
            SitemapApplier.apply( _json );
            return;
        }

        homepageMenu.updateIndices( _sitemapHomepage, _indices );
        AsyncTaskQueue.get().add( new RefreshUiTask() );
    }
}

// 1.4)
// ... and then we update the UI
//
// Refresh the UI based on the current context and menu validity:
//
// - If currently in a menu and the menu structure remains valid (as per task input),
//   refresh the current screen.
// - If the menu structure is no longer valid, navigate to the root (homepage) menu.
// - If currently in an error view, also navigate to the homepage menu.
// - If currently in the settings menu, do nothing.
class RefreshUiTask extends BaseSitemapProcessorTask {

    public function initialize() {
        BaseSitemapProcessorTask.initialize();
    }

    public function invoke() as Void {
        
        // Logger.debug( "RefreshUiTask.invoke" );

        if( ! HomepageMenu.exists() ) {
            throw new GeneralException( "HomepageMenu does not exist" );
        }

        var homepage = HomepageMenu.get();

        // A confirmation dialog is a modal view owned by the system until the
        // user responds. Changing the view stack underneath it would dismiss or
        // re-show the dialog on every sitemap update, so the UI refresh is
        // skipped in that case. The menu data has already been updated at this
        // point; the display is refreshed once the dialog is closed.
        if( ViewStack.getCurrentView()[0] instanceof WatchUi.Confirmation ) {
            // Logger.debug( "RefreshUiTask.invoke: confirmation showing, skipping UI refresh" );
            AsyncTaskQueue.get().add( new TriggerNextRequestTask() );
            return;
        }

        // If we are in the settings menu, we do nothing
        if( ! SettingsMenuManager.isShowingSettings() ) {
            // We reset to the root homepage view if:
            // - the update invalidated the structure and we are not already in the homepage view or
            // - we are in a non-sitemap view (i.e. loading or error)
            var currentView = ViewStack.getCurrentView()[0];
            if( ( ! homepage.structureRemainsValid() && ! ( currentView instanceof HomepageMenu ) )
                || ! HomepageMenu.isSitemapShowing()
            ) {
                // Logger.debug( "SitemapRequest.onReceive: resetting to homepage" );
                ViewStack.resetTo( homepage, HomepageMenuDelegate.get() );
            } else {
                // If the structure is still valid and no error is shown,
                // then we update the screen, showing the changes in the
                // currently displayed menu
                // Logger.debug( "SitemapRequest.onReceive: requesting UI update" );
                WatchUi.requestUpdate();
            }
        }
        AsyncTaskQueue.get().add( new TriggerNextRequestTask() );
    }
}

// 2)
// ... and finally, we trigger the next request
class TriggerNextRequestTask extends BaseSitemapProcessorTask {
    public function initialize() {
        BaseSitemapProcessorTask.initialize();
    }
    public function invoke() as Void {
        // Logger.debug( "TriggerNextRequestTask.invoke" );
        SitemapRequest.get().triggerNextRequest( false );
    }
}

// Applies a received sitemap: builds the new data layer, which schedules its
// own tasks, and then creates or updates the menu.
class SitemapApplier {
    public static function apply( json as SitemapJsonIncoming ) as Void {
        // Logger.debug( "SitemapApplier.apply" );

        // Note: SitemapStore.updateSitemapFromJson also refreshes the stored
        // JSON and marks the model as applied
        var sitemapHomepage = SitemapStore.updateSitemapFromJson( json );

        Logger.debug( "SitemapApplier: new data layer constructed" );

        if( ! HomepageMenu.exists() ) {
            // No menu exists yet, so we process the JSON and transition 
            // from the LoadingView to the menu.
            // This can also happen after an out-of-memory exception,
            // where the menu was cleared — in that case, we switch
            // from the ErrorView to the new menu.
            AsyncTaskQueue.get().add( new CreateHomepageTask( sitemapHomepage ) );
        } else {
            // There is already a menu, so we update it,
            // asynchronously to not block the UI
            AsyncTaskQueue.get().add( new UpdateHomepageTask( sitemapHomepage ) );
        }
    }

    // Applies a received sitemap by rebuilding only the given top level
    // elements. Returns false if no partial update is possible and the caller
    // must fall back to the full path.
    public static function applyPartial( 
        json as SitemapJsonIncoming,
        changedIndices as Array<Number>,
        elementCount as Number
    ) as Boolean {
        if( ! HomepageMenu.exists() ) {
            return false;
        }

        var homepage = HomepageMenu.get().getSitemapHomepage();
        if( homepage == null 
            || ! isComplete( homepage, changedIndices, elementCount ) 
        ) {
            return false;
        }

        SitemapStore.updateSitemapFromJsonPartial(
            json,
            homepage,
            changedIndices,
            AsyncTaskQueue.get()
        );

        Logger.debug( 
            "SitemapApplier: partial update of "
            + changedIndices.size() + " top level element(s)"
        );

        AsyncTaskQueue.get().add(
            new UpdateHomepageIndicesTask( json, homepage, changedIndices )
        );
        return true;
    }

    // True if the model already contains all top level widgets that are about
    // to be replaced. Guards against starting a partial update while the model
    // is still being built.
    private static function isComplete( 
        homepage as SitemapHomepage,
        indices as Array<Number>,
        elementCount as Number
    ) as Boolean {
        var widgetCount = homepage.getWidgets().size();
        if( widgetCount != elementCount ) {
            return false;
        }
        for( var i = 0; i < indices.size(); i++ ) {
            if( indices[i] < 0 || indices[i] >= widgetCount ) {
                return false;
            }
        }
        return true;
    }
}
