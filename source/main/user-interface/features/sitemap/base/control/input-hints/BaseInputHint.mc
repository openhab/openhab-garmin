import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * Base class for input hints.
 * There are two sub classes, one for round-shaped screens, and
 * one for rectangular screens. Both are called InputHint, and the
 * correct one is choosen via exclude annotations in monkey.jungle.
 */
(:exclForTouch)
class BaseInputHint extends Drawable {

    // Enum for type of key
    // The positions for each key are defined in 
    // BaseConfig/Config, since they are 
    // device-specific
    enum Key {
        HINT_KEY_ENTER,
        HINT_KEY_BACK,
        HINT_KEY_UP,
        HINT_KEY_DOWN
    }
    // Enum for type of input hint
    // NEUTRAL = no icon, uses the theme text color
    // CHECK = green check icon
    // CANCEL = red cancel cross
    // STOP = orange stop symbol
    // PREVIOUS = previous track for player
    // NEXT = next track for player
    enum Type {
        HINT_TYPE_NEUTRAL,
        HINT_TYPE_CHECK,
        HINT_TYPE_CANCEL,
        HINT_TYPE_STOP,
        HINT_TYPE_PLAY,
        HINT_TYPE_PAUSE,
        HINT_TYPE_PREVIOUS,
        HINT_TYPE_NEXT
    }
    // Default line width
    public const LINE_WIDTH as Number = 
        ( 0.0125 * Config.UI_SCREEN_WIDTH ).toNumber(); // line width is calculated proportionally to the screen width

    protected var _position as Number; // The position (angle for round, y coordinate for rectangular)
    protected var _color as ColorType;
    private var _icon as InputHintIcon?;
    private var _touchId as Symbol?;

    // The type is stored instead of the values derived from it, so that
    // both a change of the type and a change of the theme are applied with
    // the next draw. See refresh().
    private var _type as Type;

    // Initialize the members above
    protected function initialize( key as Key, type as Type, touchId as Symbol? ) {
        Drawable.initialize( {} );
        _position = Config.UI_INPUT_HINT_POSITIONS[key];
        _touchId = touchId;
        _type = type;
        _color = getColor();

        // Determines the color and initializes the InputHintIcon, for which
        // again there are two versions for round and rectangular screen
        refresh();
    }

    // Returns the color of the hint.
    // Currently all hint types use the text color of the theme.
    private function getColor() as ColorType {
        return ThemeManager.current.textColor;
    }

    // Returns the icon of the hint for a given type, or null if there is none.
    //
    // The theme-dependent icons are resolved from the current theme on every
    // call, because the theme can change while the app is running.
    private function getIconRezId( type as Type ) as ResourceId? {
        if( type == HINT_TYPE_CHECK ) {
            return Rez.Drawables.iconCheckHint;
        } else if( type == HINT_TYPE_CANCEL ) {
            return Rez.Drawables.iconCancelHint;
        } else if( type == HINT_TYPE_STOP ) {
            return Rez.Drawables.iconStopHint;
        } else if( type == HINT_TYPE_PLAY ) {
            return Rez.Drawables.iconPlayHint;
        } else if( type == HINT_TYPE_PAUSE ) {
            return Rez.Drawables.iconPauseHint;
        } else if( type == HINT_TYPE_PREVIOUS ) {
            return ThemeManager.current.iconPreviousHint;
        } else if( type == HINT_TYPE_NEXT ) {
            return ThemeManager.current.iconNextHint;
        }
        return null;
    }

    // Applies the color and the icon of the current type and theme.
    //
    // Subclasses must call this before drawing, because the theme can change
    // without the input hint being recreated. The icon itself only replaces
    // its bitmap if the resource has actually changed.
    protected function refresh() as Void {
        _color = getColor();

        var iconRez = getIconRezId( _type );
        if( iconRez == null ) {
            _icon = null;
        } else if( _icon == null ) {
            _icon = new InputHintIcon( iconRez, _touchId, _position, LINE_WIDTH );
        } else {
            _icon.setIcon( iconRez );
        }
    }

    // Subclasses should implement their own draw() and
    // call this draw for drawing the icon
    public function draw( dc as Dc ) as Void {
        if( _icon != null ) {
            _icon.draw( dc );
        }
    }

    // Return the touch area associated with this
    // input hint, if there is any
    public function getTouchArea() as CircularTouchArea? {
        if( _icon != null ) {
            return _icon.getTouchArea();
        } else {
            return null;
        }
    }

    // Changes the type of the input hint
    public function setType( type as Type ) as Void {
        _type = type;
        refresh();
    }
} 
