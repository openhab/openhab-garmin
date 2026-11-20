import Toybox.Lang;

/*
 * Compares a received sitemap with the stored one.
 *
 * The openHAB sitemap JSON is laid out as
 *
 *     { "name": ..., "label": ..., "link": ..., "homepage": { "widgets": [ ... ] } }
 *
 * The comparison is split so that it can be executed in several tasks: the two
 * root levels (which only hold scalars) are compared first, then each top level
 * element is compared in its own task. This keeps every task short, so that
 * user input can be handled between them, which is what the data layer creation
 * and the menu update already do.
 *
 * As soon as a difference is found, `isDifferent` is set and the remaining
 * comparisons return immediately.
 */
class SitemapComparison {

    // Set to true as soon as a difference has been found
    public var isDifferent as Boolean = false;

    // The two sitemap roots to compare
    private var _stored as JsonObject;
    private var _received as JsonObject;

    // The top level elements of both sitemaps, set by compareRoot()
    private var _storedElements as Array?;
    private var _receivedElements as Array?;
    private var _elementCount as Number = 0;

    public function initialize( stored as JsonObject, received as JsonObject ) {
        _stored = stored;
        _received = received;
    }

    /*
     * Compares everything except the top level elements, which are compared by
     * compareElement(): the sitemap root without its homepage, the homepage
     * without its elements, and the size of the element arrays.
     *
     * Returns true if a difference was found.
     */
    public function compareRoot() as Boolean {
        // The sitemap root: name, label, link
        if( _stored.size() != _received.size() 
            || ! compareOtherKeys( _stored, _received, "homepage" ) 
            || ! compareOtherKeys( _received, _stored, "homepage" ) 
        ) {
            isDifferent = true;
            return true;
        }

        // The homepage element: id, title, link, ...
        var storedPage = _stored["homepage"] as Object?;
        var receivedPage = _received["homepage"] as Object?;
        if( ! ( storedPage instanceof Dictionary ) 
            || ! ( receivedPage instanceof Dictionary ) 
        ) {
            // Unknown structure: compare everything in one go
            return compareWholeTree();
        }
        var storedPageDict = storedPage as JsonObject;
        var receivedPageDict = receivedPage as JsonObject;
        if( storedPageDict.size() != receivedPageDict.size() 
            || ! compareOtherKeys( storedPageDict, receivedPageDict, "widgets" ) 
            || ! compareOtherKeys( receivedPageDict, storedPageDict, "widgets" ) 
        ) {
            isDifferent = true;
            return true;
        }

        // The top level elements themselves
        var storedElements = storedPageDict["widgets"] as Object?;
        var receivedElements = receivedPageDict["widgets"] as Object?;
        if( ! ( storedElements instanceof Array ) 
            || ! ( receivedElements instanceof Array ) 
        ) {
            // No elements array: compare everything in one go
            return compareWholeTree();
        }

        _storedElements = storedElements as Array;
        _receivedElements = receivedElements as Array;
        if( _storedElements.size() != _receivedElements.size() ) {
            isDifferent = true;
            return true;
        }

        _elementCount = _receivedElements.size();
        return false;
    }

    // Returns the number of top level elements that have to be compared
    public function getElementCount() as Number {
        return _elementCount;
    }

    // Compares a single top level element
    public function compareElement( index as Number ) as Void {
        if( isDifferent 
            || _storedElements == null 
            || _receivedElements == null 
            || index < 0 
            || index >= _elementCount 
        ) {
            return;
        }

        if( ! JsonComparer.areEqual( 
                ( _storedElements as Array )[index] as Object?, 
                ( _receivedElements as Array )[index] as Object? 
            ) 
        ) {
            isDifferent = true;
        }
    }

    // Compares the complete sitemaps in one go, used if the expected structure
    // is not found. This cannot be split, but still allows dropping unchanged
    // responses instead of rebuilding the data layer on every poll.
    private function compareWholeTree() as Boolean {
        isDifferent = ! JsonComparer.areEqual( _stored, _received );
        return isDifferent;
    }

    // Compares all keys of `a` except `excludedKey` with the same key in `b`
    private function compareOtherKeys( 
        a as JsonObject, 
        b as JsonObject, 
        excludedKey as String 
    ) as Boolean {
        var keys = a.keys();
        for( var i = 0; i < keys.size(); i++ ) {
            var key = keys[i] as String;
            if( key.equals( excludedKey ) ) {
                continue;
            }
            if( ! JsonComparer.areEqual( a[key] as Object?, b[key] as Object? ) ) {
                return false;
            }
        }
        return true;
    }
}
