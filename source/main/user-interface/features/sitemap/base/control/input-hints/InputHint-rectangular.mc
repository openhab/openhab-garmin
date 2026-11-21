import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * InputHint implementation for rectangular, button-based devices,
 * specifically the Edge 540 and 840.
 *
 * This class draws a vertical line next to the corresponding button,
 * and, depending on the hint type, may also include an icon.
 *
 * NOTE: On the Edge 540 and 840, the ENTER button is partially located
 * above the screen area controlled by the app, so the indicator may appear
 * slightly misaligned.
 */
(:exclForTouch :exclForScreenRound)
class InputHint extends BaseInputHint {

    private const HEIGHT as Number = 
        ( 0.2 * Config.UI_SCREEN_HEIGHT ).toNumber(); // height of the indicator

    public const LINE_WIDTH as Number = 
        ( 0.020 * Config.UI_SCREEN_WIDTH ).toNumber(); // line width is calculated proportionally to the screen width

    // The coordinates for the line
    private var _x as Number;
    private var _yStart as Number;
    private var _yEnd as Number;

    public function initialize( key as BaseInputHint.Key, type as BaseInputHint.Type, touchId as Symbol? ) {
        BaseInputHint.initialize( key, type, touchId );
        
        // prepare the coordinates
        // the _position from the Config
        // identifies the middle of the vertical line
        // + positive position is shown on the right side of the screen
        // - negative position is shown on the left side of the screen
        var position = _position;
        if( position >= 0 ) {
            _x = ( Config.UI_SCREEN_WIDTH - LINE_WIDTH/2 ).toNumber();
        } else {
            _x = ( LINE_WIDTH/2 ).toNumber();
            position = position * -1;
        }
        
        _yStart = ( position - HEIGHT/2 ).toNumber();
        _yEnd = ( position + HEIGHT/2 ).toNumber();
    }

    // Draw the line
    public function draw( dc as Dc ) as Void {
        // Apply the current theme before drawing the line
        refresh();

        // Anti-alias is only available in newer SDK versions
        if( Config.UI_USE_ANTI_ALIASING && dc has :setAntiAlias ) {
            dc.setAntiAlias( true );
        }
        
        dc.setColor( _color, ThemeManager.current.backgroundColor );
        dc.setPenWidth( LINE_WIDTH );
        dc.drawLine( _x, _yStart, _x, _yEnd );

        // Draw the icon
        BaseInputHint.draw( dc );
    }
} 
