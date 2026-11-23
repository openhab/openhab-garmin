import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/**
 * Parses color values from a JSON dictionary into a ColorType.
 */
class ColorParser {

    // Parses a color field.
    // The field may be missing or empty, in which case null is returned.
    // Otherwise, it can contain either a color name (e.g., "red") or a hex code 
    // (e.g., "#FF0000"). In both cases, a ColorType is returned—specifically, 
    // a number between 0x000000 and 0xFFFFFF.
    //
    // On error, `errorMessage` is formatted with the widget label and the
    // invalid color string, so callers can include both in the message.
    public static function parse( 
        json as JsonAdapter, 
        id as String, 
        errorMessage as String,
        label as String )
    as ColorType? {
        var colorString = json.getOptionalString( id );
        if( colorString == null || colorString.equals( "" ) ) {
            return null;
        }
        var invalidColor = colorString;
        switch ( colorString ) {
            case "maroon": return 0x800000;
            case "red": return 0xff0000;
            case "orange": return 0xffa500;
            case "olive": return 0x808000;
            case "yellow": return 0xffff00;
            case "purple": return 0x800080;
            case "fuchsia": return 0xff00ff;
            case "pink": return 0xffc0cb;
            case "white": return 0xffffff;
            case "lime": return 0x00ff00;
            case "green": return 0x008000;
            case "navy": return 0x000080;
            case "blue": return 0x0000ff;
            case "teal": return 0x008080;
            case "aqua": return 0x00ffff;
            case "black": return 0x000000;
            case "silver": return 0xc0c0c0;
            case "gray": return 0x808080;
            case "gold": return 0xffd700;
        }
        var leadChar = colorString.substring( null, 1);
        if( leadChar != null && leadChar.equals( "#" ) ) {
            colorString = colorString.substring( 1, null );
            if( colorString != null ) {
                var colorNumber = ( colorString ).toNumberWithBase( 0x10 );
                if( colorNumber != null ) {
                    if( colorNumber >= 0 && colorNumber <= 16777215 ) {
                        return colorNumber;
                    }
                }
            }
        }
        throw new JsonParsingException( Lang.format( errorMessage, [ label, invalidColor ] ) );
    }
}