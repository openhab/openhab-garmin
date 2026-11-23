import Toybox.Graphics;
import Toybox.Application;
import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Timer;
import Toybox.Application.Properties;

/*
 * The glance view of the app.
 */
(:glance) 
class GlanceSitemapView extends WatchUi.GlanceView {
    
    // Glance shows a single text area
    private var _textArea as TextArea;

    // Set to true if an error occurs in onLayout
    private var _hasError as Boolean = false;

    // Constructor
    public function initialize() {
        // Logger.debug( "GlanceSitemapView.initialize" );
        GlanceView.initialize();
        
        // Initialize the text area
        // Width and height can only be set once a Dc is available
        _textArea = new TextArea( { 
            :backgroundColor => Graphics.COLOR_TRANSPARENT,
            :font => GlanceConfig.UI_GLANCE_FONTS,
            :justification => Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER
        } );
    }

    // Setup the text area and its content
    public function onLayout( dc as Dc ) as Void {
        // Logger.debug( "GlanceSitemapView.onLayout" );
        try {
            var locX = 0;
            var locY = WatchUi.LAYOUT_VALIGN_CENTER;
            var width = dc.getWidth();
            var height = dc.getHeight();

            // If offsets are defined in the constants, we apply them 
            // here (currently used for Edge devices only)
            if( GlanceConfig.UI_GLANCE_TEXT_HORIZONTAL_OFFSET != 0 ) {
                locX = GlanceConfig.UI_GLANCE_TEXT_HORIZONTAL_OFFSET;
                width -= GlanceConfig.UI_GLANCE_TEXT_HORIZONTAL_OFFSET;
            }
            if( GlanceConfig.UI_GLANCE_TEXT_VERTICAL_OFFSET != 0 ) {
                locY = GlanceConfig.UI_GLANCE_TEXT_VERTICAL_OFFSET;
                height -= GlanceConfig.UI_GLANCE_TEXT_VERTICAL_OFFSET;
            }

            // Set the layout of the text area
            _textArea.setSize( width, height );
            _textArea.setLocation( locX, locY );

            // Set the content of the text area
            // Displays either the homepage label or if it is not available a default
            var label = SitemapStore.getLabel();
            if( label != null ) {
                _textArea.setText( label );
            } else {
                _textArea.setText( Application.loadResource( Rez.Strings.GlanceDefaultText ) as String );
            }
            _hasError = false;
        } catch( ex ) {
            // Show any errors
            _hasError = true;
            var msg = ex.getErrorMessage();
            msg = msg != null
                ? msg
                : ( Application.loadResource( Rez.Strings.ErrorUnknownGlance ) as String );
            _textArea.setText( msg );
        }
    }

    public function onUpdate( dc as Dc ) as Void {
        // Update the color on every onUpdate() to handle night mode changes.
        // On Edge devices, the glance focus indicator toggles night mode
        // without calling AppBase.onNightModeChange(), so the theme must be
        // refreshed on each update.
        if( _hasError ) {
            _textArea.setColor( Graphics.COLOR_RED );
        } else {
            // In the Glance we keep it simple and work without
            // themes, since only the text color needs to be set
            // and there are only three different cases
            var deviceSettings = System.getDeviceSettings();
            _textArea.setColor( 
                deviceSettings has :isNightModeEnabled 
                ? deviceSettings.isNightModeEnabled
                  ? Graphics.COLOR_WHITE // night mode enabled
                  : Graphics.COLOR_BLACK // night mode disabled
                : Graphics.COLOR_WHITE   // device does not support night mode
            );
        }

        _textArea.draw( dc );
    }
}
