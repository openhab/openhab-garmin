import Toybox.Lang;

/*
 * Structural comparison of two values as produced by the Connect IQ JSON
 * parser (dictionaries, arrays, strings, numbers, booleans, null).
 *
 * Used to detect whether an incoming sitemap differs from the one already
 * stored, so that unchanged responses can be skipped entirely. The comparison
 * builds no sitemap objects and parses no values, which makes it far cheaper
 * than rebuilding the data layer.
 *
 * `Object.equals()` compares dictionaries and arrays by reference, so a
 * recursive comparison is required.
 */
class JsonComparer {

    // Returns true if both values have the same structure and content.
    public static function areEqual( a as Object?, b as Object? ) as Boolean {
        if( a == null || b == null ) {
            return a == null && b == null;
        }
        if( a == b ) {
            return true;
        }
        if( a instanceof Dictionary ) {
            return dictionaryEquals( a as Dictionary, b );
        }
        if( a instanceof Array ) {
            return arrayEquals( a as Array, b );
        }
        return a.equals( b );
    }

    private static function dictionaryEquals( a as Dictionary, b as Object? ) as Boolean {
        if( ! ( b instanceof Dictionary ) ) {
            return false;
        }
        var other = b as Dictionary;
        if( a.size() != other.size() ) {
            return false;
        }
        // The only allocation in this class: the key arrays returned by keys().
        var keys = a.keys();
        for( var i = 0; i < keys.size(); i++ ) {
            var key = keys[i];
            if( ! other.hasKey( key ) || ! areEqual( a[key] as Object?, other[key] as Object? ) ) {
                return false;
            }
        }
        return true;
    }

    private static function arrayEquals( a as Array, b as Object? ) as Boolean {
        if( ! ( b instanceof Array ) ) {
            return false;
        }
        var other = b as Array;
        if( a.size() != other.size() ) {
            return false;
        }
        for( var i = 0; i < a.size(); i++ ) {
            if( ! areEqual( a[i] as Object?, other[i] as Object? ) ) {
                return false;
            }
        }
        return true;
    }
}
