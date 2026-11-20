import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * A custom menu item that displays a primary text label 
 * with a secondary sublabel beneath it.
 */
class LabelMenuItem extends BaseMenuItem {

    // The values and Drawables
    private var _title as String;
    private var _subLabel as String;
    private var _labelTextArea as CachedText?;
    private var _subLabelTextArea as CachedTextArea?;

    /*
    * Constructor.
    * Initializes the base class and stores the label and sublabel.
    */
    public function initialize( label as String, subLabel as String ) {
        BaseMenuItem.initialize( null );
        _title = label;
        _subLabel = subLabel;
    }

    // Create the Drawables
    public function onLayout( dc as Dc ) as Void {
        var dcWidth = dc.getWidth();
        var yCenter = ( dc.getHeight()/2 ).toNumber();

        // Apply the left padding
        var locX = ( dcWidth * Config.UI_MENU_ITEM_PADDING_LEFT_FACTOR ).toNumber();
        // Apply the right padding
        var width = dcWidth - locX - ( dcWidth * Config.UI_MENU_ITEM_PADDING_RIGHT_FACTOR ).toNumber();

        // Create the Drawables
        // locY for label is set for the text to be on top of the center line,
        //      using the font height
        _labelTextArea = new CachedText( {
            :text => _title,
            :font => Config.UI_MENU_ITEM_FONTS[0],
            :locX => locX,
            :locY => yCenter 
                     - Graphics.getFontHeight( Config.UI_MENU_ITEM_FONTS[0] )
                     + Config.UI_MENU_SETTINGS_TEXT_ITEM_LABEL_OFFSET,
            :justification => Graphics.TEXT_JUSTIFY_LEFT,
            :width => width,
            :height => yCenter
        } );
        _subLabelTextArea = new CachedTextArea( {
            :text => _subLabel,
            :font => Config.UI_MENU_ITEM_FONTS,
            :locX => locX,
            :locY => yCenter + Config.UI_MENU_SETTINGS_TEXT_ITEM_LABEL_OFFSET,
            :justification => Graphics.TEXT_JUSTIFY_LEFT,
            :width => width,
            :height => yCenter
        } );
    }

    /*
    * Called by the base class to handle drawing.
    * This event handler is responsible for rendering the content.
    */
    public function onUpdate( dc as Dc ) as Void {
        // Colors need to be updated on every draw, since switch
        // focus change and switch between standard and night mode 
        // could happen anytime
        var color = getCurrentTheme().textColor;

        var labelTextArea = _labelTextArea;
        if( labelTextArea != null ) {
            labelTextArea.setColor( color );
            labelTextArea.draw( dc );
        }

        var subLabelTextArea = _subLabelTextArea;
        if( subLabelTextArea != null ) {
            subLabelTextArea.setColor( color );
            subLabelTextArea.draw( dc );
        }
    }

    // Set the sub label
    public function setSubLabel( subLabel as String or ResourceId or Null ) as Void {
        if( subLabel instanceof String ) {
            _subLabel = subLabel;
            if( _subLabelTextArea != null ) {
                _subLabelTextArea.setText( _subLabel );
            }
        } else {
            _subLabel = "";
        }
    }
}