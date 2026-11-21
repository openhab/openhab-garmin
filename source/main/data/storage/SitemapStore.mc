import Toybox.Lang;
import Toybox.Application.Storage;
import Toybox.Application;
import Toybox.Time;
import Toybox.System;

/*
 * `SitemapStore` holds the latest JSON sitemap data and persists it to storage 
 * when the application ends. This applies to both glance and widget modes, 
 * enabling faster startup and immediate display of current data.
 *
 * The JSON data is stored as a tuple with a timestamp indicating its age.
 * Based on this timestamp and a configurable expiry time, a flag indicating
 * whether the state is fresh is passed to the SitemapHomepage and propagated
 * to all sitemap-related classes. Error handling also uses this freshness flag,
 * determined by this class's isSitemapFresh() function.
 *
 * On older devices, the glance view may lack sufficient memory to read the 
 * full JSON from storage or handle web requests. To address this, the sitemap 
 * label is stored separately, allowing at least the label to be shown in the glance view.
 */

(:glance)
class SitemapStore  {
    // Storage field names
    private static const STORAGE_JSON as String = "sitemapJson";
    private static const STORAGE_LABEL as String = "sitemapLabel";

    // Only JSON data taking less than 80kB in memory is written to Storage.
    // Testing has shown that somewhere around 90kB, Storage.setValue
    // fails, independent of free memory.
    private static const STORAGE_JSON_MAX_SIZE as Number = 81920;

    // Current JSON/label
    // The JSON is stored together with a numeric timestamp
    typedef StoredJson as [JsonObject, Number];
    private static var _json as StoredJson?;
    private static var _title as String?;
    private static var _estimatedSitemapSize as Number = 0;

    // True if _json was received from a sitemap request and therefore differs
    // from the JSON in Storage. A JSON that was read from Storage must not be
    // written back, as its size is unknown and the write could fail.
    private static var _isJsonModified as Boolean = false;

    /*
     * Update gating
     *
     * The model shown in the menu can temporarily differ from the stored JSON:
     * - a command has been applied locally (optimistic state),
     * - the states have been hidden because the phone connection was lost,
     * - the model was restored from stale data at startup.
     * While such a divergence exists, an incoming JSON that is identical to
     * the stored one must still be applied to bring the model back in sync.
     * See ProcessIncomingJsonTask.
     *
     * The divergence is tracked with the millisecond timer so that the
     * post-command hold time can be honored with millisecond precision.
     */
    private static var _divergenceSince as Number?;
    private static var _divergenceHoldMillis as Number?;

    // Invalidates the sitemap so that it is only shown again once fresh data
    // has been successfully retrieved. Used when a sitemap error is displayed
    // as a full-screen error: without invalidating, restarting the app would
    // briefly show the outdated menu before the error happens again.
    public static function invalidateSitemap() as Void {
        Storage.deleteValue( STORAGE_JSON );
        _json = null;
        _isJsonModified = false;
    }

    // Removes all sitemap data from memory and Storage. Used when the data no
    // longer applies, for example after a settings change that points the app
    // to a different server or sitemap. Unlike invalidateSitemap(), the label
    // is removed as well, because it belongs to the previous sitemap.
    public static function clear() as Void {
        Storage.deleteValue( STORAGE_JSON );
        Storage.deleteValue( STORAGE_LABEL );
        _json = null;
        _title = null;
        _estimatedSitemapSize = 0;
        _isJsonModified = false;
        _divergenceSince = null;
        _divergenceHoldMillis = null;
    }

    // Accessor functions for the label
    public static function getLabel() as String? {
        if( _title == null ) {
            _title = Storage.getValue( STORAGE_LABEL ) as String?;
        }
        return _title;
    }

    // Returns the age of the sitemap that is currently in memory
    /*
    (:typecheck(disableGlanceCheck))
    public static function getSitemapAge() as Duration? {
        if( _json != null ) {
            var age = Time.now().subtract( new Moment( _json[1] ) );
            if( age instanceof Duration ) {
                return age;
            } else {
                throw new GeneralException( "SitemapStore.getSitemapAge: subtract returned invalid result." );
            }
        }
        return null;
    }
    */

    // Returns the age of the sitemap that is currently in memory
    (:typecheck(disableGlanceCheck))
    public static function getSitemapTimestamp() as Moment? {
        return _json != null
               ? new Moment( _json[1] )
               : null;
    }

    // Return the sitemap currently stored in memory.
    // Freshness check is not performed but passed in as parameter,
    // this allows this function to be used in the one below it.
    (:typecheck(disableGlanceCheck))
    private static function getSitemapFromMemory( isSitemapFresh as Boolean ) as SitemapHomepage? {
        if( _json != null ) {
            return new SitemapHomepage( 
                new JsonAdapter( _json[0] ), 
                isSitemapFresh, 
                false 
            );
        }
        return null;
    }

    // Return the sitemap currently stored in Memory, marked as stale
    // independent of the actual timestamp of the sitemap.
    (:typecheck(disableGlanceCheck))
    public static function getSitemapFromMemoryWithStaleStates() as SitemapHomepage? {
        return getSitemapFromMemory( false );
    }

    // Return the sitemap currently stored in Storage
    (:typecheck(disableGlanceCheck))
    public static function getSitemapFromStorage() as SitemapHomepage? {
        _json = Storage.getValue( STORAGE_JSON ) as StoredJson?;
        _isJsonModified = false;

        var isFresh = isSitemapFresh();
        if( ! isFresh ) {
            // States are hidden while the stored data is stale, so the next
            // response must be applied even if its JSON is identical.
            noteModelDivergence();
        }
        return getSitemapFromMemory( isFresh );
    }

    // Returns true if the currently held JSON's age is 
    // within the expiry the expiry time, false if it is older. 
    // Returns false if there is no JSON.
    (:typecheck(disableGlanceCheck))
    public static function isSitemapFresh() as Boolean {
        if( _json != null ) {
            var dataAge = 
                Time.now().compare( 
                    new Moment( ( _json as StoredJson )[1] ) 
                );
            // Logger.debug( "SitemapStore.isSitemapFresh=" + ( dataAge < Config.STATE_EXPIRATION_TIME ) );
            return dataAge < BaseConfig.STATE_EXPIRATION_TIME;
        } else {
            return false;
        }
    }

    // Persist the current data to storage
    // This function is called by OHApp when the application is stopped
    (:typecheck(disableGlanceCheck))
    public static function persist() as Void {
        try {
            if( _title != null ) {
                Storage.setValue( STORAGE_LABEL, _title );
                _title = null;
            }

            // Logger.debugMemory( _estimatedSitemapSize );

            if( ! _isJsonModified ) {
                return;
            }

            // Storage writes allocate memory. This turns an out-of-memory
            // error, which would otherwise be uncatchable, into an exception.
            MemoryManager.ensureSufficientMemory();

            if( _json != null && _estimatedSitemapSize <= STORAGE_JSON_MAX_SIZE ) {
                // Logger.debug( "SitemapStore: persisting JSON" );
                Storage.setValue( STORAGE_JSON, _json );
            } else if( _estimatedSitemapSize > STORAGE_JSON_MAX_SIZE ) {
                // Logger.debug( "SitemapStore: not persisting, JSON too large" );
                Storage.deleteValue( STORAGE_JSON );
            }
            _json = null;
            _isJsonModified = false;
        } catch( ex ) {
            _json = null;
            _isJsonModified = false;

            // OutOfMemoryException is thrown by MemoryManager.ensureSufficientMemory()
            // when free memory is already low. It is a normal, catchable exception;
            // the engine's own out-of-memory error is not catchable and would have
            // terminated the app before reaching this handler.
            // In this case the Storage itself is fine, so keep the sitemap that is
            // already stored there instead of throwing away the offline cache.
            if( ex instanceof OutOfMemoryException ) {
                return;
            }

            // The Storage is full. Without this handling, the exception would
            // escape the system's onStop() callback and crash the app. Free the
            // space occupied by the sitemap and write the label again, so at
            // least the glance keeps its label. The next start behaves like a
            // first start instead of displaying outdated data.
            try {
                Storage.deleteValue( STORAGE_JSON );
                if( _title != null ) {
                    Storage.setValue( STORAGE_LABEL, _title );
                }
            } catch( ex2 ) {
                // Nothing left to do, the label is lost but the app keeps running
            }
            _title = null;
        }
    }

    // Records that the current model no longer matches the stored JSON and
    // that the next response must be applied even if its JSON is unchanged.
    public static function noteModelDivergence() as Void {
        noteModelDivergenceFor( 0 );
    }

    // Same as noteModelDivergence(), but the divergence cannot be reconciled
    // before `holdMillis` (in ms) has elapsed. Used after a command, because the
    // affected item ignores updates during the configured post-command hold time.
    public static function noteModelDivergenceFor( holdMillis as Number ) as Void {
        _divergenceSince = System.getTimer();
        _divergenceHoldMillis = holdMillis;
    }

    // True if the model can be assumed to match the stored JSON.
    public static function isModelInSync() as Boolean {
        return _divergenceSince == null;
    }

    // Called whenever an incoming JSON has been applied.
    public static function markModelApplied() as Void {
        if( _divergenceSince == null ) {
            return;
        }
        var elapsed = System.getTimer() - ( _divergenceSince as Number );
        // A negative elapsed time can only occur if the millisecond timer wrapped
        // around (about every 50 days). The hold has then certainly elapsed.
        if( elapsed < 0 || elapsed >= ( _divergenceHoldMillis as Number ) ) {
            _divergenceSince = null;
            _divergenceHoldMillis = null;
        }
    }

    // Creates a comparison between the given JSON and the currently stored
    // JSON. Returns null if there is no stored JSON to compare with.
    (:typecheck(disableGlanceCheck))
    public static function createComparison( receivedJson as JsonObject ) as SitemapComparison? {
        return _json != null
               ? new SitemapComparison( ( _json as StoredJson )[0], receivedJson )
               : null;
    }

    // Keeps the stored JSON but refreshes its timestamp and size estimate,
    // e.g. when an identical response is received. The data must not age out,
    // see isSitemapFresh().
    (:typecheck(disableGlanceCheck))
    public static function refreshStored( incomingJson as SitemapJsonIncoming ) as Void {
        if( _json != null ) {
            ( _json as StoredJson )[1] = incomingJson.timestamp;
            _estimatedSitemapSize = incomingJson.estimatedSize;
        }
    }

    // Creates a new sitemap and updates the JSON
    // as well as the label
    (:typecheck(disableGlanceCheck))
    public static function updateSitemapFromJson( 
        incomingJson as SitemapJsonIncoming
    ) as SitemapHomepage {
        _json = incomingJson.getForStorage();
        _estimatedSitemapSize = incomingJson.estimatedSize;
        _isJsonModified = true;
        var isSyncInProgress = SafeSitemapSyncDelegate.isSyncInProgress();
        var homepage = new SitemapHomepage( 
            new JsonAdapter( incomingJson.json ), 
            isSyncInProgress ? false : true, // in sync mode, we mark the sitemap as stale/not fresh because we do not want to display states
            isSyncInProgress ? false : true  // in sync mode, we use synchronous processing, otherwise asynchronous processing
        );
        _title = homepage.title;
        markModelApplied();
        return homepage;
    }
}
