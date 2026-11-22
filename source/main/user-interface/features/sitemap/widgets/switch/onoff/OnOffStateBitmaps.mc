import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Provides three BufferedBitmaps representing a toggle switch in the
 * "on" and "off" positions, and without a state.
 *
 * Each BufferedBitmap is used to obtain a corresponding Dc (Drawing Context),
 * allowing the switch to be rendered using primitive shapes such as circles and rectangles.
 *
 * The bitmaps are allocated lazily, on first access, and are then reused for the
 * lifetime of the app. Themes with identical colors share one instance, see getShared().
 * If no bitmap can be allocated, the getters return null and the caller renders the
 * switch directly instead, see drawDirectly().
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

    // The colors the bitmaps are created with. They are retained so that the
    // switch can also be drawn directly if no bitmap can be allocated.
    private var _onFillColor as ColorType;
    private var _offFillColor as ColorType;
    private var _switchColor as ColorType;

    // The BufferedBitmaps for ON, OFF and no available state.
    // Created lazily on first access, null if they could not be allocated.
    private var _on as BufferedBitmapType?;
    private var _off as BufferedBitmapType?;
    private var _noState as BufferedBitmapType?;

    // True once all three bitmaps have been created successfully
    private var _bitmapsCreated as Boolean = false;

    // Instances that are shared between themes
    private static var _shared as Array<OnOffStateBitmaps> = [];

    /*
     * Returns the instance for the given colors, reusing an existing instance
     * if another theme was already created with the same colors.
     *
     * Sharing is keyed on the colors rather than on the theme class, because
     * derived themes may inherit their bitmaps from a base theme, and themes may
     * only override colors that do not feed into the toggle switch.
     */
    public static function getShared(
        onFillColor as ColorType,
        offFillColor as ColorType,
        switchColor as ColorType
    ) as OnOffStateBitmaps {
        for( var i = 0; i < _shared.size(); i++ ) {
            if( _shared[i].hasColors( onFillColor, offFillColor, switchColor ) ) {
                return _shared[i];
            }
        }
        var bitmaps = new OnOffStateBitmaps( onFillColor, offFillColor, switchColor );
        _shared.add( bitmaps );
        return bitmaps;
    }

    // Constructor
    public function initialize(
        onFillColor as ColorType,
        offFillColor as ColorType,
        switchColor as ColorType
    ) {
        _onFillColor = onFillColor;
        _offFillColor = offFillColor;
        _switchColor = switchColor;
    }

    // Returns true if this instance creates its bitmaps with the given colors.
    // Used by getShared() to find an instance that can be reused.
    //
    // Note: This method must be public. Protected methods are not resolvable
    // through dynamic dispatch, which is needed when the static factory
    // functions call it on the elements of the shared arrays.
    public function hasColors(
        onFillColor as ColorType,
        offFillColor as ColorType,
        switchColor as ColorType
    ) as Boolean {
        return
            _onFillColor == onFillColor &&
            _offFillColor == offFillColor &&
            _switchColor == switchColor;
    }

    // Returns the bitmap for the "on" state, or null if it could not be allocated
    public function getOn() as BufferedBitmapType? {
        createBitmaps();
        return _on;
    }

    // Returns the bitmap for the "off" state, or null if it could not be allocated
    public function getOff() as BufferedBitmapType? {
        createBitmaps();
        return _off;
    }

    // Returns the bitmap for the state not being available,
    // or null if it could not be allocated
    public function getNoState() as BufferedBitmapType? {
        createBitmaps();
        return _noState;
    }

    /*
     * Draws the switch directly on the given Dc, without using a buffered bitmap.
     *
     * Used as a fallback when the bitmaps could not be allocated, so that a lack
     * of memory does not prevent the switch from being rendered.
     */
    public function drawDirectly( 
        dc as Dc,
        x as Number,
        y as Number,
        isEnabled as Boolean?
    ) as Void {
        drawSwitch( 
            dc, 
            x, 
            y, 
            WIDTH, 
            HEIGHT, 
            isEnabled, 
            getFillColor( isEnabled ), 
            _switchColor 
        );

        // Restore the pen width and anti-aliasing that the drawing primitives changed,
        // because the Dc is shared with the other elements of the menu item
        dc.setPenWidth( 1 );
        if( Config.UI_USE_ANTI_ALIASING && dc has :setAntiAlias ) {
            dc.setAntiAlias( false );
        }
    }

    // Creates the bitmaps if they do not exist yet.
    //
    // If an allocation fails, partially created bitmaps are discarded so that a
    // later call retries. The getters then return null and the caller falls back
    // to drawing the switch directly.
    private function createBitmaps() as Void {
        if( _bitmapsCreated ) {
            return;
        }

        try {
            _on = draw( true, getFillColor( true ), _switchColor );
            _off = draw( false, getFillColor( false ), _switchColor );
            _noState = draw( null, getFillColor( null ), _switchColor );
            _bitmapsCreated = _on != null && _off != null && _noState != null;
        } catch( ex ) {
            _bitmapsCreated = false;
        }

        if( ! _bitmapsCreated ) {
            _on = null;
            _off = null;
            _noState = null;
        }
    }

    // Returns the color used to fill the switch outline for the given state
    private function getFillColor( isEnabled as Boolean? ) as ColorType {
        return isEnabled == true ? _onFillColor : _offFillColor;
    }

    // Draws the switch into a new buffered bitmap.
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

        drawSwitch( dc, 0, 0, WIDTH, HEIGHT, isEnabled, fillColor, switchColor );

        return bufferedBitmap;
    }

    /*
     * Draws the switch UI element with its upper/left corner at (x, y).
     *
     * The outline consists of two circles and a rectangle,
     * filled with the configured fill color.
     * A smaller circle indicates the current on/off position.
     */
    private function drawSwitch( 
        dc as Dc,
        x as Number,
        y as Number,
        width as Number,
        height as Number,
        isEnabled as Boolean?,
        fillColor as ColorType,
        switchColor as ColorType
    ) as Void {

        dc.setColor( fillColor, Graphics.COLOR_TRANSPARENT );

        // Spacing defines the gap between the outer edge of the `Drawable` and the switch.
        // This is necessary because anti-aliasing can cause drawing primitives to slightly
        // exceed their intended boundaries.
        var spacing = ( width * (1-OUTER_CIRCLE_FACTOR) / 2 ).toNumber();
        // Radius of the upper and lower circles of the switch outline
        var radius = (width/2).toNumber() - spacing;
        var xCenter = x + spacing + radius; // Horizontal center of the switch
        var upperYCenter = y + spacing + radius; // Center of the upper circle
        var lowerYCenter = y + height - spacing - radius; // Center of the lower circle
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
            var yCenter = y + ( height/2 ).toNumber();
            dc.drawLine( xCenter - innerRadius, yCenter, xCenter + innerRadius, yCenter );
        }
    }
}