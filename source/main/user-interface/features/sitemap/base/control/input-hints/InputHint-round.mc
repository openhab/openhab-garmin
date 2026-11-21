import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * InputHint implementation for round, button-based devices,
 * such as the Fenix and Forerunner series.
 *
 * This class draws an arc next to the corresponding button,
 * and, depending on the hint type, may also include an icon.
 */
(:exclForTouch :exclForScreenRectangular)
class InputHint extends BaseInputHint {

    private const RADIUS as Number = 
        ( 0.49 * Config.UI_SCREEN_WIDTH ).toNumber(); // radius of the arc is calculated relative to the screen width
    private const LENGTH as Number = 18; // total length of the arc in degree

    // All data needed for drawing the arc
    private var _xCenter as Number;
    private var _yCenter as Number;
    private var _arcRadius as Number;
    private var _arcFromAngle as Number;
    private var _arcToAngle as Number;

    // Constructor
    public function initialize( key as BaseInputHint.Key, type as BaseInputHint.Type, touchId as Symbol? ) {
        BaseInputHint.initialize( key, type, touchId );

        // Calculate all parameters for the arc
        _xCenter = ( Config.UI_SCREEN_WIDTH / 2 ).toNumber();
        _yCenter = ( Config.UI_SCREEN_HEIGHT / 2 ).toNumber();
        _arcRadius = RADIUS;
        _arcFromAngle = _position - LENGTH / 2;
        _arcToAngle = _position + LENGTH / 2;
    }

    // Draw the input hint
    public function draw( dc as Dc ) as Void {
        // Apply the current theme before drawing the arc
        refresh();

        // Anti-alias is only available in newer SDK versions
        if(  Config.UI_USE_ANTI_ALIASING && dc has :setAntiAlias ) {
            dc.setAntiAlias( true );
        }
        
        // Draw the arc
        dc.setColor( _color, ThemeManager.current.backgroundColor );
        dc.setPenWidth( LINE_WIDTH );
        dc.drawArc( _xCenter, _yCenter, _arcRadius, Graphics.ARC_COUNTER_CLOCKWISE, _arcFromAngle, _arcToAngle );

        // Draw the icon
        BaseInputHint.draw( dc );
    }
} 
