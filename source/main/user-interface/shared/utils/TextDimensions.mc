import Toybox.Lang;
import Toybox.Graphics;

/*
 * This class provides functions to calculate the width of text
 * in situations where a valid Dc (Drawing Context) is not available.
 *
 * Background: In the Garmin SDK, a Dc is required to draw on the screen.
 * It is only valid during specific event handlers like `onUpdate` in a View.
 * Unfortunately, calculating the rendered width of text in a specific font
 * requires access to a valid Dc.
 *
 * To enable text width calculation outside of these event handlers,
 * we can create a BufferedBitmap, which provides a valid Dc that supports
 * both drawing and measuring text width.
 *
 * Note: Saving a Dc instance during an event handler and reusing it later
 * will not work. Calling `getTextWidthInPixels()` outside of the handler
 * using a saved Dc will throw an exception.
 */
class TextDimensions {
    // The BufferedBitmap is a Singleton
    private static var _bufferedBitmap as BufferedBitmap?;
    
    // Gets a reference to the Singleton,
    // and creates it, if it does not exist yet
    private static function get() as BufferedBitmap {
        if( _bufferedBitmap == null ) {
            // The factory creates the BufferedBitmap using the appropriate API
            // for the device: CIQ >= 4 uses a graphics pool and resource
            // references, CIQ < 4 creates the bitmap in the app heap.
            // We store the BufferedBitmap, not the BufferedBitmap reference,
            // to ensure that the BufferedBitmap stays available.
            _bufferedBitmap = BufferedBitmapFactory.createBufferedBitmap( 
                { :width => 1, :height => 1 } 
            );
        }
        return _bufferedBitmap as BufferedBitmap;
    }
    
    // Use the Dc from the buffered bitmap to determine the text width
    public static function getTextWidthInPixels( text as String, font as FontType ) as Number {
        return get().getDc().getTextWidthInPixels( text, font );
    }

    // Use the Dc from the buffered bitmap to determine the height of a font
    public static function getFontHeight( font as FontType ) as Number {
        return get().getDc().getFontHeight( font );
    }

    // This function calculates the width ratio of one string (text1) towards
    // the sum of itself and a second string (text2)
    public static function getWidthRatio( text1 as String, text2 as String ) as Float {
        var len1 = getTextWidthInPixels( text1, Graphics.FONT_MEDIUM );
        var len2 = getTextWidthInPixels( text2, Graphics.FONT_MEDIUM );
        return len1.toFloat()/(len1.toFloat()+len2.toFloat());
    }
}