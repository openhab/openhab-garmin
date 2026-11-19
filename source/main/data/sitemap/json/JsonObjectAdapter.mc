import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * The `JsonAdapter` wraps a `JsonObject` and provides functions
 * to access data within it.
 *
 * It is a recursive structure that also provides access to nested
 * `JsonAdapter` instances, as well as arrays of such objects.
 *
 * For many types, the class offers two kinds of accessors:
 * - One for mandatory fields, such as `getString()`, which takes an
 *   error message and throws an exception if the field is missing.
 * - One for optional fields, such as `getOptionalString()`, which
 *   returns null if the field is not present.
 */
typedef JsonAdapterArray as Array<JsonAdapter>;

class JsonAdapter {

    private var _jsonObject as JsonObject;

    // Constructor
    public function initialize( jsonObject as JsonObject ) {
        _jsonObject = jsonObject;
    }

    // Returns a JsonArray from a given JsonObject, 
    // or an error message if the value is not present
    public function getArray( id as String, errorMessage as String ) as JsonAdapterArray {
        var value = getOptionalArray( id );
        if( value == null ) {
            throw new JsonParsingException( errorMessage );
        }
        return value;
    }

    // Returns a Boolean from a given JsonObject, 
    // defaults to false if the value is not present
    public function getBoolean( id as String ) as Boolean {
        var value = _jsonObject[id];
        if( value != null && ! ( value instanceof Boolean ) ) {
            throw new JsonParsingException( "'" + id + "' is not a Boolean" );
        }
        return value == null ? false : value;
    }
    
    // Returns a Float from a given JsonObject, 
    // or defaults to the passed in def value if the value is not present
    // When reading the value from the JSON, all types of numerics are
    // accepted and converted to Float.
    public function getFloat( id as String, def as Float ) as Float {
        
        var value = _jsonObject[id];

        if( value == null ) {
            return def;
        } else if( value instanceof Float ) {
            return value;
        } else if( value instanceof Number || value instanceof Long || value instanceof Double ) {
            return value.toFloat();
        } else if( value instanceof String ) {
            value = value.toFloat();
            if( value != null ) {
                return value;
            }
        }
        throw new JsonParsingException( "'" + id + "' is not numeric" );
        
    }

    /* getNumber is currently not needed, since Setpoint and Slider
       widgets were updated to support floats.

    // Returns a Number from a given JsonObject, 
    // or defaults to the passed in def value if the value is not present
    // On some phyiscal watches numbers from the JSON are read as floats, even if
    // they do not have a decimal point. Therefore this function accepts all
    // types of numerics as well as strings that can be converted into a number.
    public function getNumber( id as String, def as Number ) as Number {
        var value = _jsonObject[id];

        if( value == null ) {
            return def;
        } else if( value instanceof Number ) {
            return value;
        } else if( value instanceof Long || value instanceof Float || value instanceof Double ) {
            return value.toNumber();
        } else if( value instanceof String ) {
            value = value.toNumber();
            if( value != null ) {
                return value;
            }
        }
        throw new JsonParsingException( "'" + id + "' is not numeric" );
        
    }
    */

    // Returns another JsonObject from a given JsonObject, 
    // or an error message if the value is not present
    public function getObject( id as String, errorMessage as String ) as JsonAdapter {
        var value = getOptionalObject( id );
        if( value == null ) {
            throw new JsonParsingException( errorMessage );
        }
        return value;
    }

    // Returns a JsonArray from a given JsonObject, 
    // or an error message if the value is not present
    public function getOptionalArray( id as String ) as JsonAdapterArray? {
        var value = _jsonObject[id];
        if( value != null && ! ( value instanceof Array ) ) {
            throw new JsonParsingException( "'" + id + "' is not an Array" );
        }
        if( value == null || value.size() == 0 ) {
            return null;
        }
        var jsonArray = value as JsonArray;
        var result = new JsonAdapterArray[0];
        for( var i = 0; i < jsonArray.size(); i++ ) {
            result.add( new JsonAdapter( jsonArray[i]) );
        }
        return result;
    }

    // Returns another JsonObject from a given JsonObject, 
    // or an error message if the value is not present
    public function getOptionalObject( id as String ) as JsonAdapter? {
        var value = _jsonObject[id];
        if( value != null ) {
            if( ! ( value instanceof Dictionary ) ) {
                throw new JsonParsingException( "'" + id + "' is not a Dictionary" );
            }
            return new JsonAdapter( value as JsonObject );
        } else {
            return null;
        }
    }

    // Returns a String from a given JsonObject, 
    // allowing empty strings, and also returning an empty string
    // if the field is not present
    public function getOptionalString( id as String ) as String {
        var value = _jsonObject[id];
        if( value != null && ! ( value instanceof String ) ) {
            throw new JsonParsingException( "'" + id + "' is not a String" );
        }
        return value == null ? "" : value;
    }

    // Returns a String from a given JsonObject, 
    // or an error message if the value is not present
    public function getString( id as String, errorMessage as String ) as String {
        var value = getOptionalString( id );
        if( value.equals( "" ) ) {
            throw new JsonParsingException( errorMessage );
        }
        return value;
    }

    // Returns a shallow copy of the wrapped object without the given key.
    // Used by derived sitemap elements, which must not inherit properties
    // such as a linked page from the server-provided element.
    public function copyWithout( excludedId as String ) as JsonAdapter {
        var copy = {} as JsonObject;
        var keys = _jsonObject.keys();
        for( var i = 0; i < keys.size(); i++ ) {
            var key = keys[i];
            if( ! key.equals( excludedId ) ) {
                copy[key] = _jsonObject[key];
            }
        }
        return new JsonAdapter( copy );
    }

    // Sets a String value in the wrapped object. Intended for derived
    // elements whose label is computed by the app.
    public function setString( id as String, value as String ) as Void {
        _jsonObject[id] = value;
    }

}