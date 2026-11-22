import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * `BaseMenuItem` serves as the base class for all menu item implementations.
 *
 * Its current responsibility is to draw the focus indicator on devices that require it 
 * (e.g., Edge 540/840). Additional shared functionality may be added in the future.
 *
 * Subclasses must implement the `onLayout()` and `onUpdate()` methods, which mimic 
 * the behavior of the Garmin SDK`s View class. 
 * - `onLayout()` is called once before the first update to perform layout calculations.
 * - `onUpdate()` is called each time the menu item needs to be redrawn.
 */

class BaseMenuItem extends CustomMenuItem {
    
    // Set to true once onLayout() has been called
    private var _hasLayout as Boolean = false;

    // Constructor
    // This base class currently only uses the `id` from the options;
    // all other options are used by derived classes.
    protected function initialize( id as Object? ) {
        // Logger.debug( "BaseMenuItem.initialize: id=" + id );
        CustomMenuItem.initialize( id, {} );
    }

    // Draw the menu item.
    //
    // This method draws the divider and delegates further layout 
    // and drawing to subclass implementations.
    public function draw( dc as Dc ) as Void {
        try {
            if( ! _hasLayout ) {
                _hasLayout = true;
                onLayout( dc );
            }

            var theme = getCurrentTheme();
            
            dc.setColor( theme.textColor, theme.menuItemBackgroundColor );
            dc.clear();

            // Draw the menu item divider
            if( Config.UI_MENU_ITEM_SHOW_DIVIDER ) {
                dc.setPenWidth( 2 );
                var lineY = dc.getHeight() - 1;
                dc.setColor( ThemeManager.current.menuItemDividerColor, ThemeManager.current.menuItemBackgroundColor );
                dc.drawLine( 0, lineY, dc.getWidth(), lineY );
                dc.setColor( theme.textColor, theme.menuItemBackgroundColor );
            }

            // Draw a vertical line on the left side of the menu item
            // as a workaround for a firmware bug on button-based Edge x50 devices.
            // See the Config class for details about those devices.
            if( Config.UI_MENU_FOCUS_EDGEX50_SHOW_ALTERNATE_FOCUS_INDICATOR ) {
                dc.setPenWidth( Config.UI_SCREEN_WIDTH * 0.02 );
                dc.setColor( ThemeManager.current.stateColor, ThemeManager.current.menuItemBackgroundColor );
                dc.drawLine( 0, 0, 0, dc.getHeight() );
                dc.setColor( theme.textColor, theme.menuItemBackgroundColor );
            }

            onUpdate( dc );

        } catch( ex ) {
            ExceptionHandler.handleBackgroundException( ex );
        }
    }

    // Returns the theme to apply when drawing.
    //
    // Subclasses should use this method to retrieve the theme and
    // apply colors on every onUpdate() call, as the theme may change at any time.
    public function getCurrentTheme() as Theme {
        return isFocused()
               ? ThemeManager.getFocusedTheme()
               : ThemeManager.current;
    }

    // May be implemented by subclasses to perform layout calculations
    public function onLayout( dc as Dc ) as Void;

    // Must be implemented by sub-classes, to draw their content
    public function onUpdate( dc as Dc ) as Void {
        throw new AbstractMethodException( "BaseMenuItem.drawImpl" );
    }

}