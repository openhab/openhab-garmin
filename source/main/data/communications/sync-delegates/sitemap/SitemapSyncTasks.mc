import Toybox.Lang;

/*
 * This file defines all asynchronous task classes that are specific to sitemap updates
 * performed via the sync delegate over Wi-Fi.
 *
 * Most of the update process is handled by the standard tasks that process sitemap request
 * responses and create or update the HomepageMenu.
 *
 * These tasks adjust their behavior when running in sync mode. Since UI updates must not
 * be performed in sync mode, a StopSitemapSyncTask is queued instead. This task ends sync
 * mode and schedules a PostSyncTask, which restarts the sitemap request that was
 * paused when sync mode was entered and updates the UI.
 */

/*
 * Asynchronous task to stop sync mode after processing of the sitemap update was completed.
 */
 class StopSitemapSyncTask extends BaseSitemapProcessorTask {

    public function initialize() {
        BaseSitemapProcessorTask.initialize();
    }

    public function invoke() as Void {
        // Logger.debug( "StopSitemapSyncTask: stopping sync mode" );
        SitemapSyncDelegate.get().finishSync();
    }
}


/*
 * Asynchronous task to restart the sitemap request after the sync mode has been closed.
 */
 class PostSyncTask extends BaseSitemapProcessorTask {

    public function initialize() {
        BaseSitemapProcessorTask.initialize();
    }

    public function invoke() as Void {
        // Logger.debug( "PostSyncTask: restarting sitemap request" );
        SitemapRequest.get().requestNow();
    }
}
