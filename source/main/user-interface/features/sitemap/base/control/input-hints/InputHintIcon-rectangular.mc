import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * This class draws the icon for input hints. As with the InputHint
 * class, there are two implementations: one for round screens,
 * and another for rectangular screens (this one).
 *
 * This class also accepts a touch area identifier. If provided,
 * it will create a corresponding touch area, which is automatically
 * added by ControlView.
 */
(:exclForTouch :exclForScreenRound)
class InputHintIcon extends Bitmap {

    // The currently assigned icon resource.
    // Kept to avoid reassigning the bitmap on every draw.
    private var _icon as ResourceId;

    // The touch area
    private var _touchArea as CircularTouchArea?;
    public function getTouchArea() as CircularTouchArea? {
        return _touchArea;
    }
    
    public function initialize( 
        icon as ResourceId,
        touchId as Symbol?, 
        position as Number, 
        lineWidth as Number ) 
    {
        _icon = icon;

        // Initialize the bitmap
        Bitmap.initialize( {
            :rezId => icon
        } );

        // The icon is drawn next to the vertical input hint
        // with a little bit of spacing (one linewidth accounts
        // for the line, the second is spacing)
        // + positive position is shown on the right side of the screen
        // - negative position is shown on the left side of the screen
        if( position >= 0 ) {
            locX = Config.UI_SCREEN_WIDTH - lineWidth*2 - width;
        } else {
            locX = lineWidth*2;
            position = position * -1;
        }
        
        locY = ( position - height/2 ).toNumber();

        // If a touch area identifier was provided,
        // then create one
        if( touchId != null ) {
            _touchArea = new CircularTouchArea( 
                touchId, 
                ( locX + width/2 ).toNumber(), 
                position, 
                ( width*2 ).toNumber()
            );
        }
    }

    // Replaces the icon resource, e.g. after a theme change.
    //
    // Both theme variants of an icon have the same dimensions, therefore
    // the position and the touch area do not need to be recalculated.
    // If the resource is unchanged, the bitmap is left untouched.
    public function setIcon( icon as ResourceId ) as Void {
        if( _icon != icon ) {
            _icon = icon;
            setBitmap( icon );
        }
    }
} 
