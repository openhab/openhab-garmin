import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Bitmap implementation for rendering an play/pause bitmap, based on the item state.
 * This class works both for button- and touch-based devices.
 */
class PlayPauseBitmap extends InteractiveRelativeBitmap {

    // Options accepted by this class
    typedef Options as { 
        :state as String, // the current state
        :identifier as Lang.Object, 
        :locX as Lang.Numeric, 
        :locY as Lang.Numeric, 
        :width as Lang.Numeric, 
        :height as Lang.Numeric, 
        :visible as Lang.Boolean,
        :touchId as Symbol?
    };

    // Storing the state helps us determining if there was an
    // actual change of state, when setState is called.
    private var _currentState as String;

    // The theme the bitmap was last resolved for.
    // On button-based devices the icons are provided by the theme and
    // therefore change with the night mode. See updateTheme().
    private var _theme as Theme;

    // Constructor
    // Processes the initial state
    public function initialize( options as InteractiveRelativeBitmap.Options ) {
        _currentState = options[:state] as String;
        _theme = ThemeManager.current;
        options[:rezId] = getResourceId( _currentState );
        InteractiveRelativeBitmap.initialize( options );
    }

    // Returns the ResourceId of the bitmap to be shown for a specific state.
    //
    // The resources are resolved on every call, because they can depend on
    // the theme, which may change while the view is open.
    private function getResourceId ( currentState as String ) as ResourceId {
        return currentState.equals( SwitchItem.ITEM_STATE_PLAY )
            ? getPlayingRezId()
            : getPausedRezId();
    }

    // The icon shown in the PLAY state
    (:exclForTouch)
    private function getPlayingRezId() as ResourceId {
        return ThemeManager.current.iconPlayButton;
    }
    (:exclForButton)
    private function getPlayingRezId() as ResourceId {
        // The touch icons are not theme-dependent
        return Rez.Drawables.iconPauseTouch;
    }

    // The icon shown in the PAUSE state
    (:exclForTouch)
    private function getPausedRezId() as ResourceId {
        return ThemeManager.current.iconPauseButton;
    }
    (:exclForButton)
    private function getPausedRezId() as ResourceId {
        // The touch icons are not theme-dependent
        return Rez.Drawables.iconPlayTouch;
    }

    // setState is called with every sitemap update
    // To improve performance, we only switch the BufferedBitmap
    // if the state changed
    public function setState( state as String ) as Void {
        if( ! _currentState.equals( state ) ) {
            _currentState = state;
            setBitmap( getResourceId( state ) );
        }
    }

    // Applies the current theme to the bitmap.
    //
    // Called before drawing, because the theme can change while the view
    // is open. Nothing is recreated while the theme stays the same.
    public function updateTheme() as Void {
        var theme = ThemeManager.current;
        if( _theme != theme ) {
            _theme = theme;
            setBitmap( getResourceId( _currentState ) );
        }
    }
}