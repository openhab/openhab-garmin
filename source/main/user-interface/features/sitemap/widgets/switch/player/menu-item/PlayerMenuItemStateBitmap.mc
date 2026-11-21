import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Drawable for rendering a play/pause state icon as part of the `PlayerMenuItem`.
 */
class PlayerMenuItemStateBitmap extends Bitmap {

    // Storing the state helps us determining if there was an
    // actual change of state, when setEnabled is called.
    private var _isPlaying as Boolean;

    // The theme the bitmap was last resolved for.
    // The icon is provided by the theme, so it changes with the night mode.
    private var _theme as Theme;

    // Constructor
    // Processes the initial state
    public function initialize( isPlaying as Boolean ) {
        _isPlaying = isPlaying;
        _theme = ThemeManager.current;

        Bitmap.initialize( {
            :rezId => getPlayPauseBitmap( isPlaying )
        } );
    }

    // setPlaying is called with every sitemap update
    // To improve performance, we only switch the bitmap
    // if the state changed
    public function setPlaying( isPlaying as Boolean ) as Void {
        if( _isPlaying != isPlaying ) {
            _isPlaying = isPlaying;
            setBitmap( getPlayPauseBitmap( isPlaying ) );
        }
    }

    // Applies the current theme to the bitmap.
    //
    // Called before drawing, because the theme can change without the
    // playback state changing. Nothing is recreated while the theme
    // stays the same.
    public function updateTheme() as Void {
        var theme = ThemeManager.current;
        if( _theme != theme ) {
            _theme = theme;
            setBitmap( getPlayPauseBitmap( _isPlaying ) );
        }
    }

    // Returns the right resource id for a given state
    private function getPlayPauseBitmap( isPlaying as Boolean ) as ResourceId {
        return isPlaying == true
            ? ThemeManager.current.iconPlayMenuItem
            : ThemeManager.current.iconPauseMenuItem;
    }
}