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
 *    If no menu exists yet, this task initiates:
 *      1.1) CreateHomepageTask:
 *           Initializes the menu structure using asynchronous tasks.
 *      1.2) SwitchToHomepageTask:
 *           Switches from the current view to the `HomepageMenu`.
 *
 *    If a menu already exists, it initiates:
 *      1.3) UpdateHomepageTask:
 *           Updates the existing menu structure asynchronously.
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
// and a instantiates a SitemapHomepage to reepresent it
// This is a recursive process, and SitemapPage, the base
// class of SitemapHomepage, will create tasks on its own
// for the processing. These tasks are added to THE FRONT
// OF THE QUEUE, and therefore will be executed before 
// the next step is triggered.
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
            && SitemapStore.matchesStored( _json.json )
        ) {
            // The JSON is unchanged, but its age must be refreshed so that
            // the data does not expire while no update is applied.
            SitemapStore.refreshStored( _json );
            Logger.debug( "ProcessIncomingJsonTask: update dropped, sitemap unchanged" );
            AsyncTaskQueue.get().add( new TriggerNextRequestTask() );
            return;
        }

        Logger.debug( "ProcessIncomingJsonTask: new data layer constructed" );

        var sitemapHomepage = SitemapStore.updateSitemapFromJson( _json );

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
