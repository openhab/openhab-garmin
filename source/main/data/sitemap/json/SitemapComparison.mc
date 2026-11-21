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
 * All top level elements are evaluated, because the indices of the changed
 * elements are needed to apply a partial update. A difference sets
 * `isDifferent`, but does not stop the remaining comparisons.
 *
 * A changed `widgetId` at the same index indicates that the elements were
 * reordered or replaced rather than updated in place. Such structural changes
 * are applied with the full rebuild path.
 */
class SitemapComparison {

    // Set to true as soon as a difference has been found
    public var isDifferent as Boolean = false;

    // Set to true if a changed element no longer has the same widgetId,
    // which indicates a reorder or a replacement instead of an update.
    private var _isReordered as Boolean = false;

    // Indices of the top level elements that differ
    private var _changedIndices as Array<Number> = [];

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

    // Compares a single top level element. Every element has to be evaluated
    // because the changed indices are needed for a partial update.
    public function compareElement( index as Number ) as Void {
        if( _storedElements == null 
            || _receivedElements == null 
            || index < 0 
            || index >= _elementCount 
        ) {
            return;
        }

        var storedElement = ( _storedElements as Array )[index] as Object?;
        var receivedElement = ( _receivedElements as Array )[index] as Object?;
        if( JsonComparer.areEqual( storedElement, receivedElement ) ) {
            return;
        }

        isDifferent = true;
        _changedIndices.add( index );

        // A different widgetId at the same position means that the element
        // was not only updated, but moved or replaced. The partial update
        // cannot express this reliably, so the full path is used.
        if( hasDifferentWidgetId( storedElement, receivedElement ) ) {
            _isReordered = true;
        }
    }

    // Indices of the top level elements that differ
    public function getChangedIndices() as Array<Number> {
        return _changedIndices;
    }

    // True if at least one top level element differs
    public function hasChangedElements() as Boolean {
        return _changedIndices.size() > 0;
    }

    // True if a changed element indicates a reorder or replacement
    public function isReordered() as Boolean {
        return _isReordered;
    }

    // Returns the widgetId of a top level element, or null if it is not
    // available.
    private static function getWidgetId( element as Object? ) as String? {
        if( ! ( element instanceof Dictionary ) ) {
            return null;
        }
        var widgetId = ( element as Dictionary )["widgetId"];
        return widgetId instanceof String ? widgetId as String : null;
    }

    // True if both elements have a widgetId and it differs.
    private static function hasDifferentWidgetId( 
        a as Object?, 
        b as Object? 
    ) as Boolean {
        var storedId = getWidgetId( a );
        var receivedId = getWidgetId( b );
        return storedId != null 
            && receivedId != null 
            && ! storedId.equals( receivedId );
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
