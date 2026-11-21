import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Drawable for rendering an on/off toggle switch.
 *
 * Uses the three bitmaps provided by OnOffStateBitmaps and updates
 * the BufferedBitmap in its base class based on the current toggle state.
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

        BufferedBitmapDrawable.initialize( {
            :bufferedBitmap => getOnOffBitmap( isEnabled, useSmallIcon, _theme )
        } );
    }


    // Returns the theme to apply when drawing.
    private function getCurrentTheme( isFocused as Boolean ) as Theme {
        return isFocused
               ? ThemeManager.focused
               : ThemeManager.current;
    }


    // setEnabledAndIconSize is called with every sitemap update
    // To improve performance, we only switch the BufferedBitmap
    // if the state changed
    public function setEnabledAndIconSize( isEnabled as Boolean?, useSmallIcon as Boolean ) as Void {
        if( _isEnabled != isEnabled || _useSmallIcon != useSmallIcon ) {
            _isEnabled = isEnabled;
            _useSmallIcon = useSmallIcon;
            setBufferedBitmap( getOnOffBitmap( isEnabled, useSmallIcon, _theme ) );
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
            setBufferedBitmap( getOnOffBitmap( _isEnabled, _useSmallIcon, _theme ) );
        }
    }


    // Returns the right bitmap for a given state
    private function getOnOffBitmap( 
        isEnabled as Boolean?, 
        useSmallIcon as Boolean,
        theme as Theme
    ) as BufferedBitmapType {

        var bitmaps = useSmallIcon
                        ? theme.smallOnOffBitmaps
                        : theme.onOffBitmaps;

        return isEnabled == null
                ? bitmaps.nostate
                : isEnabled
                    ? bitmaps.on
                    : bitmaps.off;
    }

}