import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * Prior to CIQ 4.0.0, `BufferedBitmap` lacked `getWidth()` and `getHeight()` methods,
 * making it incompatible with the `Bitmap` Drawable.
 * To handle this, we implement our own `BufferedBitmapDrawable` class,
 * which subclasses `Drawable` and accepts either a CIQ 4.0.0+ `BufferedBitmap`
 * or a `LegacyBufferedBitmap` for devices running earlier versions.
 *
 * NOTE: An initial workaround involved passing a `LegacyBufferedBitmap`
 * into a `Bitmap` Drawable. While this compiled and worked in the simulator,
 * it failed on real devices:
 * https://github.com/openhab/openhab-garmin/issues/89
 */

// The options for this Drawable
typedef BufferedBitmapDrawableOptions as { 
    :bufferedBitmap as BufferedBitmapType,
    :identifier as Lang.Object, 
    :locX as Lang.Numeric, 
    :locY as Lang.Numeric, 
    :visible as Lang.Boolean 
};

class BufferedBitmapDrawable extends Drawable {

    private var _bufferedBitmap as BufferedBitmapType;

    // Constructor
    // We fill width and height from the buffered bitmap,
    // and pass it with the other options to the Drawable base class.
    public function initialize( options as BufferedBitmapDrawableOptions ) {
        _bufferedBitmap = options[:bufferedBitmap] as BufferedBitmapType;
        options[:width] = _bufferedBitmap.getWidth();
        options[:height] = _bufferedBitmap.getHeight();
        Drawable.initialize( options );
    }

    // When the bitmap is updated, we update the size
    // in the Drawable base class
    public function setBufferedBitmap( 
        bufferedBitmap as BufferedBitmapType 
    ) as Void {
        _bufferedBitmap = bufferedBitmap;
        setSize( 
            bufferedBitmap.getWidth(), 
            bufferedBitmap.getHeight()
        );
    }

    // Draws the buffered bitmap on a Dc.
    // locX and locY can be either absolute coordinates or layout constants.
    // If layout constants are used, we must compute the actual x/y drawing positions manually.
    public function draw( dc as Dc ) as Void {
        if( isVisible ) {
            var x = locX;
            var y = locY;

            // drawBitmap takes the coordinates of the upper/left
            // corner of the bitmap. x/y are computed accordingly
            if( x == WatchUi.LAYOUT_HALIGN_CENTER ) {
                x = dc.getWidth()/2 - width/2;
            } else if( x == WatchUi.LAYOUT_HALIGN_LEFT || x == WatchUi.LAYOUT_HALIGN_START ) {
                x = 0;
            } else if( x == WatchUi.LAYOUT_HALIGN_RIGHT ) {
                x = dc.getWidth() - width;
            }

            if( y == WatchUi.LAYOUT_VALIGN_CENTER ) {
                y = dc.getHeight()/2 - height/2;
            } else if( y == WatchUi.LAYOUT_VALIGN_TOP || y == WatchUi.LAYOUT_VALIGN_START ) {
                y = 0;
            } else if( y == WatchUi.LAYOUT_VALIGN_BOTTOM ) {
                x = dc.getHeight() - height;
            }

            dc.drawBitmap( x, y, _bufferedBitmap );
        }
    }
}