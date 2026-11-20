import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * A custom menu item for the `CommandMenu`. It displays the label of a command
 * and stores the actual command for processing by the delegate.
 * It used only on Edge devices, where the `ActionMenu` is not available.
 */
(:exclForActionMenu)
class CommandOptionMenuItem extends BaseMenuItem {

    // The values and Drawable
    private var _label as String;
    private var _command as String;
    private var _labelTextArea as CachedText?;

    /*
    * Constructor.
    * Initializes the base class and stores the label and command.
    */
    public function initialize( label as String, command as String ) {
        BaseMenuItem.initialize( null );
        _label = label;
        _command = command;
    }

    // Returns the command
    public function getCommand() as String {
        return _command;
    }

    // Create the Drawable
    public function onLayout( dc as Dc ) as Void {
        var dcWidth = dc.getWidth();
        var dcHeight = dc.getHeight();

        var font = Config.UI_MENU_ITEM_FONTS[0];

        var yCenter = dcHeight/2 - Graphics.getFontHeight( font )/2 + Graphics.getFontDescent( font )/2;

        // Apply the left padding
        var locX = ( dcWidth * Config.UI_MENU_ITEM_PADDING_LEFT_FACTOR ).toNumber();
        // Apply the right padding
        var width = dcWidth - locX - ( dcWidth * Config.UI_MENU_ITEM_PADDING_RIGHT_FACTOR ).toNumber();

        // Create the Drawables
        _labelTextArea = new CachedText( {
            :text => _label,
            :font => font,
            :locX => locX,
            :locY => yCenter,
            :justification => Graphics.TEXT_JUSTIFY_LEFT,
            :width => width,
            :height => dcHeight
        } );
    }

    /*
    * Called by the base class to handle drawing.
    * This event handler is responsible for rendering the content.
    */
    public function onUpdate( dc as Dc ) as Void {
        var theme = getCurrentTheme();
        var labelTextArea = _labelTextArea;
        if( labelTextArea != null ) {
            labelTextArea.setColor( theme.textColor );
            labelTextArea.setBackgroundColor( theme.menuItemBackgroundColor );
            labelTextArea.draw( dc );
        }
    }
}