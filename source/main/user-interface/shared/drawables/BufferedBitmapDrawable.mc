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
    :bufferedBitmap as BufferedBitmapType?,
    :identifier as Lang.Object, 
    :locX as Lang.Numeric, 
    :locY as Lang.Numeric, 
    :visible as Lang.Boolean,
    :width as Lang.Numeric,
    :height as Lang.Numeric
};

class BufferedBitmapDrawable extends Drawable {

    // The bitmap to draw, or null if none is available
    private var _bufferedBitmap as BufferedBitmapType?;

    // Constructor
    //
    // If a bitmap is provided, its dimensions are used for the Drawable size.
    // Subclasses that are able to render without a bitmap may pass null and
    // provide the width and height instead. draw() then calls
    // drawWithoutBitmap(), which such subclasses override.
    public function initialize( options as BufferedBitmapDrawableOptions ) {
        var bufferedBitmap = options[:bufferedBitmap];
        _bufferedBitmap = bufferedBitmap;
        if( bufferedBitmap != null ) {
            options[:width] = bufferedBitmap.getWidth();
            options[:height] = bufferedBitmap.getHeight();
        }
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

    // Removes the bitmap while keeping the current size, so that a subclass
    // can render within the same bounds. draw() then calls drawWithoutBitmap().
    // Used as a fallback when no bitmap could be allocated.
    public function clearBufferedBitmap() as Void {
        _bufferedBitmap = null;
    }

    // Draws the buffered bitmap on a Dc.
    // locX and locY can be either absolute coordinates or layout constants.
    // If layout constants are used, we must compute the actual x/y drawing positions manually.
    public function draw( dc as Dc ) as Void {
        if( isVisible ) {
            var x = resolveX( dc );
            var y = resolveY( dc );
            var bufferedBitmap = _bufferedBitmap;

            if( bufferedBitmap == null ) {
                drawWithoutBitmap( dc, x, y );
            } else {
                dc.drawBitmap( x, y, bufferedBitmap );
            }
        }
    }

    // Called by draw() when no bitmap is available.
    //
    // The base implementation does nothing. Subclasses that can render
    // without a bitmap override this to draw their content directly.
    protected function drawWithoutBitmap( 
        dc as Dc, 
        x as Lang.Numeric, 
        y as Lang.Numeric 
    ) as Void {}

    // Resolves the horizontal position for drawing, taking layout constants into account
    private function resolveX( dc as Dc ) as Lang.Numeric {
        var x = locX;
        // drawBitmap takes the coordinates of the upper/left
        // corner of the bitmap. x/y are computed accordingly
        if( x == WatchUi.LAYOUT_HALIGN_CENTER ) {
            x = dc.getWidth()/2 - width/2;
        } else if( x == WatchUi.LAYOUT_HALIGN_LEFT || x == WatchUi.LAYOUT_HALIGN_START ) {
            x = 0;
        } else if( x == WatchUi.LAYOUT_HALIGN_RIGHT ) {
            x = dc.getWidth() - width;
        }
        return x;
    }

    // Resolves the vertical position for drawing, taking layout constants into account
    private function resolveY( dc as Dc ) as Lang.Numeric {
        var y = locY;
        if( y == WatchUi.LAYOUT_VALIGN_CENTER ) {
            y = dc.getHeight()/2 - height/2;
        } else if( y == WatchUi.LAYOUT_VALIGN_TOP || y == WatchUi.LAYOUT_VALIGN_START ) {
            y = 0;
        } else if( y == WatchUi.LAYOUT_VALIGN_BOTTOM ) {
            y = dc.getHeight() - height;
        }
        return y;
    }
}