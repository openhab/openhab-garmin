import Toybox.Lang;
import Toybox.Application;
import Toybox.Communications;
import Toybox.WatchUi;

/*
 * Sync delegate for updating the sitemap via Wi-Fi.
 *
 * IMPORTANT: When calling functions of this class from code that may also run
 * on devices without Wi-Fi support, always use SafeSitemapSyncDelegate as a wrapper.
 * See that class for details.
 */
class SitemapSyncDelegate extends BaseSyncDelegate {

    /******* STATIC *******/ 

    // Singleton instance and accessor
    private static var _instance as SitemapSyncDelegate?;

    public static function get() as SitemapSyncDelegate {
        if( _instance == null ) {
            _instance = new SitemapSyncDelegate();
        }
        return _instance as SitemapSyncDelegate;
    }

    /******* INSTANCE *******/ 

    // Constructor
    private function initialize() {
        BaseSyncDelegate.initialize();
    }

    // Overrides a method from the parent class to clean up the sitemap request.
    // Must not be called from outside. The method is public only due to
    // a Monkey C limitation that prevents overriding protected or
    // private methods.
    public function onSyncFinished() as Void {
        // Logger.debug( "SitemapSyncDelegate: onSyncFinished" );
        BaseSyncDelegate.onSyncFinished();
        SitemapRequest.resetSyncInstance();
    }

    // Called by the app to request an update of the sitemap in sync mode
    public function requestSitemapUpdate() as Void {
        // Logger.debug( "SitemapSyncDelegate: requestSitemapUpdate" );
        startSync( Application.loadResource( Rez.Strings.SyncUpdatingSitemap ) as String );
    }

    // Called by the base class to perform the actual sync tasks.
    // Must not be called from outside. The method is public only due to
    // a Monkey C limitation that prevents overriding protected or
    // private methods.
    public function performSync() as Void {
        // Logger.debug( "SitemapSyncDelegate: performSync" );

        // Note: get() returns the sync instance of the sitemap request
        SitemapRequest.get().makeRequestSingle();
    }
}