import Toybox.Lang;

/*
 * Compares a received sitemap with the stored one.
 *
 * The comparison is split so that it can be executed in several tasks: the
 * root of the sitemap (which holds the title and similar scalars) is compared
 * first, then each top level element is compared in its own task. This keeps
 * every task short, so that user input can be handled between them, which is
 * what the data layer creation and the menu update already do.
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
     * Compares the root of both sitemaps, i.e. everything except the top level
     * elements, which are compared by compareElement().
     *
     * Returns true if a difference was found.
     */
    public function compareRoot() as Boolean {
        if( _stored.size() != _received.size() 
            || ! compareRootKeys( _stored, _received ) 
            || ! compareRootKeys( _received, _stored ) 
        ) {
            isDifferent = true;
            return true;
        }

        var storedElements = _stored["widgets"];
        var receivedElements = _received["widgets"];
        if( ! ( storedElements instanceof Array ) 
            || ! ( receivedElements instanceof Array ) 
        ) {
            isDifferent = true;
            return true;
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

    // Compares all root keys except the top level elements of `a` with `b`
    private function compareRootKeys( a as JsonObject, b as JsonObject ) as Boolean {
        var keys = a.keys();
        for( var i = 0; i < keys.size(); i++ ) {
            var key = keys[i] as String;
            if( key.equals( "widgets" ) ) {
                continue;
            }
            if( ! JsonComparer.areEqual( a[key] as Object?, b[key] as Object? ) ) {
                return false;
            }
        }
        return true;
    }
}
