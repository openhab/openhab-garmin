import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;
import Toybox.System;

/*
 * Custom Bitmap Drawable, extending the standard Bitmap.
 *
 * This class supports defining a touch area for the bitmap,
 * enabling ControlViewDelegate to handle touch events on it.
 *
 * Additionally it allows locX and locY to be specified as a
 * percentage of the view's width and height. Any float value ≤ 1.0
 * is interpreted as a percentage, and locX/locY will be adjusted accordingly.
 * NOTE: When locX/locY are specified as percentages, they are interpreted
 * as the center of the bitmap, rather than the upper-left corner (which is standard).
 *
 */
class InteractiveRelativeBitmap extends Bitmap {

    // The standard Bitmap options are extended by
    // an optional id for the touch area for this Bitmap    
    typedef Options as { 
        :rezId as Lang.ResourceId, 
        :bitmap as Graphics.BitmapType, 
        :identifier as Lang.Object, 
        :locX as Lang.Numeric, 
        :locY as Lang.Numeric, 
        :width as Lang.Numeric, 
        :height as Lang.Numeric, 
        :visible as Lang.Boolean,
        :touchId as Symbol?
    };

    private var _touchId as Symbol?;

    // The currently assigned resource. Kept to avoid reassigning the
    // bitmap if the resource did not change, see setResourceId().
    private var _rezId as ResourceId?;

    // Store the touchId
    public function initialize( options as Options ) {
        Bitmap.initialize( options );
        _touchId = options[:touchId];
        _rezId = options[:rezId];
    }

    // Replaces the bitmap with the given resource, e.g. after a theme change.
    //
    // The bitmap is only replaced if the resource actually changed, so this
    // can be called on every draw. Both variants of a theme-dependent icon
    // have the same dimensions, so the location remains valid.
    public function setResourceId( rezId as ResourceId ) as Void {
        if( _rezId != rezId ) {
            _rezId = rezId;
            setBitmap( rezId );
        }
    }

    // locX and locY are adjusted only when
    // drawing, since they may be changed
    // anytime. Also they are public, so intercepting
    // setLocation is not sufficient to catch any
    // changes
    public function draw( dc as Dc ) as Void {
        if( locX instanceof Float && locX <= 1.0 ) {
            locX = dc.getWidth()*locX - width/2;
        }
        if( locY instanceof Float && locY <= 1.0 ) {
            locY = dc.getHeight()*locY - height/2;
        }
        Bitmap.draw( dc );
    }

    // Create the touch area
    public function createTouchArea() as CircularTouchArea? {
        if( _touchId == null ) {
            return null;
        } else {
            // Calculate the center of the bitmap
            var x = locX;
            if( x instanceof Float && x <= 1.0 ) {
                x = Config.UI_SCREEN_WIDTH * x;
            } else {
                x += width/2;
            }
            var y = locY;
            if( y instanceof Float && y <= 1.0 ) {
                y = Config.UI_SCREEN_HEIGHT * y;
            } else {
                y += height/2;
            }

            // And create the touch area
            // Currently the area radius is set to 
            // a constant 1.5xbitmap width
            // In future this could be passed in as 
            // parameter
            return new CircularTouchArea( 
                _touchId as Symbol, 
                x.toNumber(), 
                y.toNumber(), 
                ( width*1.5 ).toNumber() 
            );
        }
    }
}