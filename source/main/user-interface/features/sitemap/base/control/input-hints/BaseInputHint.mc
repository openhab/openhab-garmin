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
    // NEUTRAL = white
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
    // Association of Type with colors
    private const UI_INPUT_HINT_COLORS as Array<ColorType> = [
        ThemeManager.current.textColor,
        ThemeManager.current.textColor,
        ThemeManager.current.textColor,
        ThemeManager.current.textColor,
        ThemeManager.current.textColor,
        ThemeManager.current.textColor,
        ThemeManager.current.textColor,
        ThemeManager.current.textColor
    ];
    // Association of Type with icons
    private const UI_INPUT_HINT_ICONS as Array<ResourceId?> = [
        null,
        Rez.Drawables.iconCheckHint,
        Rez.Drawables.iconCancelHint,
        Rez.Drawables.iconStopHint,
        Rez.Drawables.iconPlayHint,
        Rez.Drawables.iconPauseHint,
        ThemeManager.current.iconPreviousHint,
        ThemeManager.current.iconNextHint
    ];

    // Default line width
    public const LINE_WIDTH as Number = 
        ( 0.0125 * Config.UI_SCREEN_WIDTH ).toNumber(); // line width is calculated proportionally to the screen width

    protected var _position as Number; // The position (angle for round, y coordinate for rectangular)
    protected var _color as ColorType;
    private var _icon as InputHintIcon?;
    private var _touchId as Symbol?;

    // Initialize the members above
    protected function initialize( key as Key, type as Type, touchId as Symbol? ) {
        Drawable.initialize( {} );
        _position = Config.UI_INPUT_HINT_POSITIONS[key];
        _color = UI_INPUT_HINT_COLORS[type];
        _touchId = touchId;
        
        // For the icon we initialize the InputHintIcon, for which
        // again there are two versions for round and rectangular screen
        var iconRez = UI_INPUT_HINT_ICONS[type];
        if( iconRez != null ) {
            _icon = new InputHintIcon( iconRez, touchId, _position, LINE_WIDTH );
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
        _color = UI_INPUT_HINT_COLORS[type];
        var iconRez = UI_INPUT_HINT_ICONS[type];
        if( iconRez != null ) {
            _icon = new InputHintIcon( iconRez, _touchId, _position, LINE_WIDTH );
        }
    }
} 
