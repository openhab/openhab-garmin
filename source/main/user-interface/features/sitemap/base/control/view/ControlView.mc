import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Control views provide specialized controls for specific widgets or item types.
 * This is the base class for all control views. It maintains a list of Drawables
 * that compose the control view, and a list of touch areas associated with actions.
 *
 * Subclasses should use addDrawable() to define their layout—ideally within onLayout().
 *
 * Subclasses can override onUpdate(), but should call the base class implementation
 * to ensure the Drawables are properly rendered.
 *
 * Use addTouchArea() to define areas that trigger actions when tapped 
 * (see ControlViewDelegate). If a InteractiveRelativeBitmap with a touch area is added,
 * the area is automatically included.
 *
 * Subclasses can also use addInputHint() to display input hints on button-based devices—
 * indicators showing which buttons perform which actions.
 */
class ControlView extends View {

    // Constructor
    public function initialize() {
        View.initialize();
    }

    // The list of Drawables
    private var _drawables as Array<Drawable> = [];
    
    // Add a Drawable
    public function addDrawable( drawable as Drawable ) as Void {
        _drawables.add( drawable );
        // If this is a InteractiveRelativeBitmap with a defined touch area,
        // add its touch area to the list
        if( drawable instanceof InteractiveRelativeBitmap ) {
            var touchArea = drawable.createTouchArea();
            if( touchArea != null ) {
                addTouchArea( touchArea );
            }
        }
    }

    // This function allows subclasses to add a title TextArea and a
    // connection mode indicator at a standardized position at the
    // top of the screen.
    protected function addTitleAndConnectionIndicator( 
        title as String, 
        dcWidth as Number, 
        dcHeight as Number 
    ) as TextArea {
        
        // Calculate locY of the title
        var titleLocY = dcHeight * 0.075;

        // Create and add the title Drawable
        var titleDrawable = new TextArea( {
            :text => title,
            :font => Config.UI_PICKER_TITLE_FONTS,
            :justification => Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER,
            :locX => WatchUi.LAYOUT_HALIGN_CENTER,
            :locY => titleLocY,
            :width => dcWidth * 0.5,
            :height => dcHeight * 0.2
        } );
        addDrawable( titleDrawable );

        // Create and add the connection mode indicator,
        addDrawable( new ConnectionModeIndicator(  ConnectionModeIndicator.LOCATION_CUSTOM_VIEW ) );

        return titleDrawable;
    }

    // Draw all Drawables
    public function draw( dc as Dc ) as Void {
        for( var i = 0; i < _drawables.size(); i++ ) {
            var drawable = _drawables[i];
            if( drawable instanceof Text || drawable instanceof TextArea ) {
                drawable.setColor( ThemeManager.current.textColor );
            }
            drawable.draw( dc );
        }
    }
    
    // Clear the Dc and draw all Drawables    
    public function onUpdate( dc as Dc ) as Void {
        // Logger.debug( "ControlView.onUpdate: start" );

        // We need to clear the clip, because there is bug in Garmin SDK,
        // with a clip in the menu title setting a clip in subsequent views
        // being displayed. See here for more details:
        // https://github.com/openhab/openhab-garmin/issues/81
        dc.clearClip();

        dc.setColor( ThemeManager.current.textColor, ThemeManager.current.backgroundColor );
        dc.clear();

        draw( dc );
        // Logger.debug( "ControlView.onUpdate: end" );
    }

    // Touch areas with accessors
    private var _touchAreas as Array<TouchArea> = [];
    public function addTouchArea( ta as TouchArea ) as Void {
        _touchAreas.add( ta );
    }
    public function getTouchAreas() as Array<TouchArea> {
        return _touchAreas;
    }

    // Add an input hint
    // Key identifies for which key (enter or back) the hint shall be displayed
    // Type defines the color and icon
    // Set touchId to associate the input hint icon with a touch behavior
    (:exclForTouch)
    public function addInputHint( 
        key as BaseInputHint.Key, 
        type as BaseInputHint.Type, 
        touchId as Symbol 
    ) as InputHint {
        var inputHint = new InputHint( 
            key,
            type,
            touchId 
        );
        addDrawable( inputHint );
        // The input hint will associate the touchId with a
        // touch area, but ONLY IF it has an icon
        var touchArea = inputHint.getTouchArea();
        if( touchArea != null ) {
            addTouchArea( touchArea );
        }
        return inputHint;
    }
}