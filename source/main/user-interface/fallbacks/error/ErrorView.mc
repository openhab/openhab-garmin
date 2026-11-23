import Toybox.Graphics;
import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Lang;
import Toybox.Math;
import Toybox.System;

/*
 * View for displaying a full-screen error message resulting from an exception.
 *
 * It is implemented as a Singleton with static member functions responsible 
 * for displaying, updating, and hiding the error view.
 */
 class ErrorView extends WatchUi.View {

    /******* STATIC *******/
    
    /*
    * Retrieves the error view to display an error message.
    * If no error view is currently shown, a new one is created; 
    * otherwise, the existing view is updated.
    *
    * This function is used by `OHApp` for errors that occur during startup, 
    * as well as by the `showOrUpdate()` function below.
    */
    public static function createOrUpdate( ex as Exception ) as ErrorView {
        var errorView = get();

        if( errorView == null ) {
            // Logger.debug( "ErrorView.createOrUpdate: creating error view" );
            errorView = new ErrorView( ex );
        } else {
            errorView.update( ex );
            // Logger.debug( "ErrorView.createOrUpdate: updating error view" );
        }
        return errorView;
    }

    // Returns the error view if is shown, or otherwise null
    private static function get() as ErrorView? {
        var view = ViewStack.getCurrentView()[0];
        if( view instanceof ErrorView ) {
            return view;
        } else {
            return null;
        }
    }

    // Determins whether an error view is currently showing
    public static function isShowing() as Boolean {
        return ViewStack.getCurrentView()[0] instanceof ErrorView;
    }

    /*
    * Call this function when an error must be shown as a full-screen error view.
    * If an error view is already displayed, it will be updated; otherwise, a new one 
    * will be created and shown.
    */
    public static function showOrUpdate( ex as Exception ) as Void {
        // We use the function above, and only if the error view
        // was not yet displayed switch to it
        var alreadyShowsErrorView = isShowing();
        var errorView = createOrUpdate( ex );
        if( ! alreadyShowsErrorView ) {
            // Logger.debug( "ErrorView.showOrUpdate: switching to error view" );
            ViewStack.resetTo( errorView, null );
        }
    }

    /******* INSTANCE *******/
    
    // The exception to be displayed
    private var _exception as Exception;

    // Constructor
    private function initialize( exception as Exception ) {
        View.initialize();
        _exception = exception;
    }

    /*
    * While the error view is active, toast notifications for non-fatal errors 
    * should be suppressed. 
    * See `ExceptionHandler` and `WarningToastHandler` for related logic.
    */
    public function onShow() as Void {
        WarningToastHandler.setUseToasts( false );
    }

    /*
    * While the error view is displayed, sitemap requests continue to run.
    * Any new errors that occur will update the content of the error view.
    */
    public function update( exception as Exception ) as Void {
        _exception = exception;
        WatchUi.requestUpdate();
    }

    // Draw the error
    public function onUpdate( dc as Dc ) as Void {
        // Logger.debug( "ErrorView.onUpdate" );
        // Logger.debugMemory( null );
        
        // We need to clear the clip, because there is bug in Garmin SDK,
        // with a clip in the menu title setting a clip in subsequent views
        // being displayed. See here for more details:
        // https://github.com/openhab/openhab-garmin/issues/81
        dc.clearClip();


        // Set the colors
        dc.setColor( ThemeManager.current.errorColor, ThemeManager.current.backgroundColor );
        dc.clear();

        drawTextArea( dc );
    }

    // Draw the error message in a TextArea
    private function drawTextArea( dc as Dc ) as Void {
        // Define the error message
        var text = _exception.getErrorMessage();
        text = ( text == null || text.equals( "" ) )
            ? ( Application.loadResource( Rez.Strings.ErrorUnknownException ) as String )
            : text;

        // Dimensions of the text area
        // Start with the full rectangle Dc ...
        var width = dc.getWidth();
        var height = dc.getHeight();

        // ... and for round screens, adapt it to the
        // largest square fitting into the circle
        if( Config.UI_SCREEN_SHAPE == System.SCREEN_SHAPE_ROUND ) {
            width = width / Math.sqrt( 2 );
            height = width;
        }
        
        // Create the textarea and draw it.
        // The TextArea could also be created only once and reused,
        // but let's say for error handling performance is not critical.
        // The first opening of the error view would need to create it anyway
        // and for subsequent updates it does not really matter how fast they are
        new TextArea( {
            :text => text,
            :font => Config.UI_ERROR_FONTS,
            :locX => WatchUi.LAYOUT_HALIGN_CENTER,
            :locY => WatchUi.LAYOUT_VALIGN_CENTER,
            :justification => Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER,
            :color => ThemeManager.current.errorColor,
            :backgroundColor => ThemeManager.current.backgroundColor,
            :width => width,
            :height => height
        } ).draw( dc );
    }

}
