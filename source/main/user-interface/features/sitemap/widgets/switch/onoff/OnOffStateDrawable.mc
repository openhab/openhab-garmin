import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Drawable for rendering an on/off toggle switch.
 *
 * Uses the bitmaps provided by OnOffStateBitmaps and updates the
 * BufferedBitmap in its base class based on the current toggle state.
 *
 * If no bitmap can be allocated for the active theme, the switch is
 * drawn directly instead, see BufferedBitmapDrawable.drawWithoutBitmap().
 */
class OnOffStateDrawable extends BufferedBitmapDrawable {


    // Storing the state helps us determining if there was an
    // actual change of state, when setEnabled and setFocus is called.
    private var _isEnabled as Boolean?;
    private var _useSmallIcon as Boolean;
    private var _theme as Theme;


    // Constructor
    // Processes the initial state
    public function initialize( 
        isEnabled as Boolean?, 
        isFocused as Boolean, 
        useSmallIcon as Boolean 
    ) {
        _isEnabled = isEnabled;
        _useSmallIcon = useSmallIcon;
        _theme = getCurrentTheme( isFocused );

        var bitmaps = getBitmaps( _useSmallIcon, _theme );
        BufferedBitmapDrawable.initialize( {
            :bufferedBitmap => getOnOffBitmap( bitmaps, isEnabled ),
            :width => bitmaps.WIDTH,
            :height => bitmaps.HEIGHT
        } );
    }


    // Returns the theme to apply when drawing.
    private function getCurrentTheme( isFocused as Boolean ) as Theme {
        return isFocused
               ? ThemeManager.getFocusedTheme()
               : ThemeManager.current;
    }


    // Returns the bitmaps to apply when drawing.
    // The themes share the bitmap sets, so switching between themes
    // does not create additional bitmaps.
    private function getBitmaps( useSmallIcon as Boolean, theme as Theme ) as OnOffStateBitmaps {
        return useSmallIcon
               ? theme.smallOnOffBitmaps
               : theme.onOffBitmaps;
    }


    // setEnabledAndIconSize is called with every sitemap update
    // To improve performance, we only switch the BufferedBitmap
    // if the state changed
    public function setEnabledAndIconSize( isEnabled as Boolean?, useSmallIcon as Boolean ) as Void {
        if( _isEnabled != isEnabled || _useSmallIcon != useSmallIcon ) {
            _isEnabled = isEnabled;
            _useSmallIcon = useSmallIcon;
            updateBitmap();
        }
    }


    // updateFocus is called on every draw, since both the focus state and
    // the light/dark mode can change at any time.
    //
    // To improve performance, the BufferedBitmap is only switched if the
    // theme has actually changed.
    public function updateTheme( isFocused as Boolean ) as Void {
        var currentTheme = getCurrentTheme( isFocused );
        if( _theme != currentTheme ) {
            _theme = currentTheme;
            updateBitmap();
        }
    }


    // Updates the bitmap from the current state, icon size and theme.
    //
    // The update sequence matches the one used when the bitmaps are available:
    // only setBufferedBitmap() is called, which also applies the bitmap size.
    // If no bitmap could be allocated, the bitmap is removed and the size is
    // kept in sync explicitly, so that drawWithoutBitmap() renders within the
    // correct bounds.
    private function updateBitmap() as Void {
        var bitmaps = getBitmaps( _useSmallIcon, _theme );
        var bitmap = getOnOffBitmap( bitmaps, _isEnabled );
        if( bitmap == null ) {
            clearBufferedBitmap();
            setSize( bitmaps.WIDTH, bitmaps.HEIGHT );
        } else {
            setBufferedBitmap( bitmap );
        }
    }


    // Called by the base class if no BufferedBitmap is available.
    // Renders the switch directly, so that an allocation failure
    // does not prevent the menu item from being drawn.
    protected function drawWithoutBitmap( 
        dc as Dc, 
        x as Lang.Numeric, 
        y as Lang.Numeric 
    ) as Void {
        var bitmaps = getBitmaps( _useSmallIcon, _theme );
        bitmaps.drawDirectly( dc, x.toNumber(), y.toNumber(), _isEnabled );
    }


    // Returns the right bitmap for a given state
    private function getOnOffBitmap( 
        bitmaps as OnOffStateBitmaps,
        isEnabled as Boolean?
    ) as BufferedBitmapType? {
        return isEnabled == null
                ? bitmaps.getNoState()
                : isEnabled
                    ? bitmaps.getOn()
                    : bitmaps.getOff();
    }

}