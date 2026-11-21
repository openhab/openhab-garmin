import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Singleton class that provides three BufferedBitmaps representing
 * a toggle switch in the "on" and "off" positions, and without a state.
 *
 * Each BufferedBitmap is used to obtain a corresponding Dc (Drawing Context),
 * allowing the switch to be rendered using primitive shapes such as circles and rectangles.
 */
class OnOffStateBitmaps {
    
    // Height and width of the switch is defined relative to the menu item height
    // Note: The sizing must be controlled via public member variables,
    // as only those can be overridden by the SmallOnOffStateBitmaps class.
    public var HEIGHT as Number = Config.UI_MENU_ITEM_TOGGLE_SWITCH_HEIGHT;
    public var WIDTH as Number = Config.UI_MENU_ITEM_TOGGLE_SWITCH_WIDTH;

    // The circles are again defines as a factor to the width (Config.UI_MENU_ITEM_TOGGLE_SWITCH_WIDTH)
    private const OUTER_CIRCLE_FACTOR = 0.8;
    private const INNER_CIRCLE_FACTOR = 0.75;
    
    // Factors applied to the inner-circle radius, to determine
    // the size of the line displayed if there is no state
    private const NO_STATE_LINE_LENGTH_FACTOR = 0.7;
    private const NO_STATE_LINE_WIDTH_FACTOR = 0.6;

    // The BufferedBitmaps for ON, OFF and no available state
    public var on as BufferedBitmapType;
    public var off as BufferedBitmapType;
    public var nostate as BufferedBitmapType;

    // Constructor
    public function initialize( 
        onFillColor as ColorType, 
        offFillColor as ColorType,
        switchColor as ColorType 
    ) {
        on = draw( true, onFillColor, switchColor );
        off = draw( false, offFillColor, switchColor );
        nostate = draw( null, offFillColor, switchColor );
    }

    // Draw the switch UI element.
    // The outline consists of two circles and a rectangle,
    // filled with the configured fill color.
    // A smaller black circle indicates the current on/off position.
    protected function draw( 
        isEnabled as Boolean?,
        fillColor as ColorType,
        switchColor as ColorType
    ) as BufferedBitmapType {
        
        // Note: The sizing must be controlled via WIDTH and HEIGHT,
        // as those are overridden by the SmallOnOffStateBitmaps class.
        var bufferedBitmap = BufferedBitmapFactory.createBufferedBitmap( {
            :width => WIDTH,
            :height => HEIGHT,
        } );

        var dc = bufferedBitmap.getDc();
        dc.clear();

        dc.setColor( fillColor, Graphics.COLOR_TRANSPARENT );

        // Spacing defines the gap between the outer edge of the `Drawable` and the switch.
        // This is necessary because anti-aliasing can cause drawing primitives to slightly 
        // exceed their intended boundaries.
        var spacing = ( dc.getWidth() * (1-OUTER_CIRCLE_FACTOR) / 2 ).toNumber();
        // Radius of the upper and lower circles of the switch outline
        var radius = (dc.getWidth()/2).toNumber() - spacing;
        var xCenter = spacing + radius; // Horizontal center of the switch
        var upperYCenter = xCenter; // Center of the upper circle
        var lowerYCenter = dc.getHeight() - spacing - radius; // Center of the lower circle
        dc.setPenWidth( 1 );
        if(  Config.UI_USE_ANTI_ALIASING && dc has :setAntiAlias ) {
            dc.setAntiAlias( true );
        }
        dc.fillCircle( xCenter, upperYCenter, radius );
        dc.fillCircle( xCenter, lowerYCenter, radius );
        // Correction values -1 and + 3 have been determined by
        // trial and error and tested on different devices
        dc.fillRectangle( xCenter-radius-1, upperYCenter, radius*2 + 3, lowerYCenter - upperYCenter );

        // draw the inner circle showing the switch state

        dc.setColor( switchColor, Graphics.COLOR_TRANSPARENT );
        // See the comment on the first setFill
        // dc.setFill( 0xFF000000 + Graphics.COLOR_BLACK );

        var innerRadius = radius * INNER_CIRCLE_FACTOR;
        if( isEnabled != null ) {
            var toggleCenter = isEnabled ? upperYCenter : lowerYCenter;
            dc.fillCircle( xCenter, toggleCenter, innerRadius );
        } else {
            dc.setPenWidth( innerRadius * NO_STATE_LINE_WIDTH_FACTOR );
            innerRadius = innerRadius * NO_STATE_LINE_LENGTH_FACTOR;
            var yCenter = ( dc.getHeight()/2 ).toNumber();
            dc.drawLine( xCenter - innerRadius, yCenter, xCenter + innerRadius, yCenter );
        }

        return bufferedBitmap;
    }
}