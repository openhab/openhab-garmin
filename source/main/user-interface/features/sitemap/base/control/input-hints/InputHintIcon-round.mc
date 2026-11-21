import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * This class draws the icon for input hints. As with the InputHint
 * class, there are two implementations: one for round screens (this one),
 * and another for rectangular screens.
 *
 * This class also accepts a touch area identifier. If provided,
 * it will create a corresponding touch area, which is automatically
 * added by ControlView.
 */
(:exclForTouch :exclForScreenRectangular)
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
        angle as Number, 
        lineWidth as Number ) 
    {
        _icon = icon;

        // Initialize the base class
        Bitmap.initialize( {
            :rezId => icon
        } );

        
        // For calculationg the coordinates, we
        // need the diagonal of the bitmap
        var diagonal = Math.sqrt(
            CustomMath.square( width )
            + CustomMath.square( height )
        ).toNumber();

        // Initialize coordinates
        var xCenterIcon = ( Config.UI_SCREEN_WIDTH / 2 ).toNumber();
        var yCenterIcon = ( Config.UI_SCREEN_HEIGHT / 2 ).toNumber();
        // The distance from the screen center to the center of the icon
        // Factor 1.1 is to keep a bit distance between the input hint and
        // the icon
        var centerToCenter = xCenterIcon - lineWidth - ( diagonal / 2 * 1.4 ).toNumber();

        // Use trigonometry to calculate center position of the hint
        // Source for formulas: http://elsenaju.info/Rechnen/Trigonometrie.htm
        
        // For the Math functions, degrees need to be converted to radians
        var radian = Math.toRadians( angle );
        xCenterIcon = xCenterIcon + centerToCenter * Math.cos( radian );
        yCenterIcon = yCenterIcon - centerToCenter * Math.sin( radian );

        locX = ( xCenterIcon - width/2 ).toNumber();
        locY = ( yCenterIcon - height/2 ).toNumber();

        // If a touch idea was provided create a
        // circular touch area that extends a bit
        // beyond the icon
        if( touchId != null ) {
            _touchArea = new CircularTouchArea( 
                touchId, 
                xCenterIcon.toNumber(), 
                yCenterIcon.toNumber(), 
                ( diagonal * 0.75 ).toNumber()
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
