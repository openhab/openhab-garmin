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
 *
 * Numeric values are compared by value, not by type: Monkey C's `==` operator
 * promotes Number, Float, Long and Double, so 5 == 5.0 is true. This matters
 * because the JSON parser maps the same literal to Number or Float depending
 * on the device/runtime, and a Storage round-trip may change the type as well
 * (see issues #216 and #355). `Object.equals()` is type-strict for numbers
 * (1.equals( 1.0 ) is false), so mixed numeric types rely on `==`; the
 * `equals()` fallback only adds the remaining scalar cases, such as equal
 * String contents. Numeric values are deliberately not normalised, as that
 * would traverse the whole payload on every poll.
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
            var aValue = a[key] as Object?;
            var bValue = other[key] as Object?;
            // A missing key also yields null. Only if the value in `a` is null
            // can an absent key in `b` be mistaken for an equal one, so the
            // (relatively expensive) key lookup is limited to that case.
            if( aValue == null && ! other.hasKey( key ) ) {
                return false;
            }
            if( ! areEqual( aValue, bValue ) ) {
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
