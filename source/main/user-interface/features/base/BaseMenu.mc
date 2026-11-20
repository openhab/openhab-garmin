import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.System;
import Toybox.Graphics;

/*
 * Defines the options that can be passed to this `CustomMenu` implementation.
 */
typedef BaseMenuOptions as {
    :title as String,       // title shown in the header
    :itemHeight as Number,  // height of each menu item
    :footer as Drawable?    // footer Drawable to display logo/icons in the footer
};

/*
 * Base class for all `CustomMenu` implementations.
 *
 * As of now, this includes:
 * - `SettingsMenu`
 * - `PageMenu` (used for the sitemap homepage and other pages)
 */
class BaseMenu extends CustomMenu {

    // The Drawable for the title
    private var _title as CachedText;
    
    // The Drawable for the connection mode indicator
    private var _cmi as ConnectionModeIndicator = new ConnectionModeIndicator( ConnectionModeIndicator.LOCATION_MENU );

    // Constructor
    protected function initialize( options as BaseMenuOptions ) {
        // Logger.debug( "BaseMenu.initialize" );
        // Logger.debug( "BaseMenu.initialize: Homepage=" + ( self instanceof HomepageMenu ) );
        // Logger.debug( "BaseMenu.initialize: Page=" + ( self instanceof PageMenu ) );
        // Logger.debug( "BaseMenu.initialize: Settings=" + ( self instanceof SettingsMenu ) );
        // A title string is provided as input, and this section creates 
        // the corresponding `Drawable` required by the `CustomMenu` base class.
        _title = new CachedText( {
            :text => options[:title] as String,
            :font => Config.UI_MENU_TITLE_FONT
        } );
        
        // Each subclass may provide its own footer drawable.
        // If none is specified, the footer defaults to the openHAB logo.
        var footer = options[:footer] as Drawable?;
        if( footer == null ) {
            footer = new Bitmap( {
                    :rezId => ThemeManager.current.logoOpenhabText,
                    :locX => WatchUi.LAYOUT_HALIGN_CENTER,
                    :locY => WatchUi.LAYOUT_VALIGN_CENTER } );
        }

        /*
        * Now we start assembling the options for the base class, beginning with the footer.
        * Note that the title is not passed to the base class here;
        * instead, this class implements `drawTitle` (see further below).
        */
        var parentOptions = {
            :footer => footer
        };

        /*
        * The heights of the title and footer are defined by a constant.
        * If the constant is set to -1, no height values are passed to the base class, 
        * which causes the device defaults to be used.
        *
        * For most devices, the default values are applied. Currently, only Garmin Edge 
        * devices override this behavior via their implementation of the `Config` class.
        */
        if( Config.UI_MENU_TITLE_HEIGHT != -1 ) {
            parentOptions[:titleItemHeight] = Config.UI_MENU_TITLE_HEIGHT;
        }
        if( Config.UI_MENU_FOOTER_HEIGHT != -1 ) {
            parentOptions[:footerItemHeight] = Config.UI_MENU_FOOTER_HEIGHT;
        }

        // Initialize the base class
        CustomMenu.initialize( 
            options[:itemHeight] as Number,
            ThemeManager.current.backgroundColor, 
            parentOptions 
        );
    }

    // The base class already defines setTitle() with a Drawable argument,
    // so a different method name must be used here.
    public function setTitleAsString( title as String ) as Void {
        _title.setText( title );
    }

    /*
    * The title needs a background color, which isn't possible when passing 
    * a Drawable to the base class. 
    * Instead, we override `drawTitle()`, which provides full access 
    * to draw directly on the title area using a `Dc`.
    */
    public function drawTitle( dc as Dc ) as Void {
        // Logger.debug( "BaseMenu.drawTitle" );
        try {
            var dcWidth = dc.getWidth();
            var dcHeight = dc.getHeight();

            /*
            * For most devices, we avoid coloring the entire title area to leave a small black bar 
            * separating the colored section from the menu items.
            * However, on some devices—particularly Garmin Edge models—we fill the entire area.
            * The height of the background-colored region is defined in the device-specific 
            * `Config` implementation.
            */
            var clipHeight = dcHeight * Config.UI_MENU_TITLE_CLIP_FACTOR;

            /*
            * This code causes an issue where the full `Dc` size of the title is incorrectly 
            * applied as the clip area to any subsequently displayed `View` classes 
            * (except when the next view is a `CustomMenu`, which is unaffected).
            *
            * As a workaround, affected views must explicitly call `Dc.clearClip()` 
            * to reset the clipping region.
            */
            dc.setColor( ThemeManager.current.textColor, ThemeManager.current.menuTitleBackgroundColor );
            dc.setClip( 0, 0, dcWidth, clipHeight );
            dc.clear();
            dc.clearClip();

            // On rectangular screens, we draw a divider between menu title and menu items
            if( Config.UI_SCREEN_SHAPE == Toybox.System.SCREEN_SHAPE_RECTANGLE ) {
                dc.setPenWidth( 2 );
                var lineY = dcHeight - 1;
                dc.setColor( ThemeManager.current.menuTitleDividerColor, ThemeManager.current.menuTitleBackgroundColor );
                dc.drawLine( 0, lineY, dcWidth, lineY );
            }

            /*
            * The first time this function is called, we set the Y-position of the title `Drawable`.
            *
            * On Edge devices (with full-height titles), slightly offsetting the title downward looks better.
            * We use half the font descent for a subtle adjustment.
            *
            * On round watch faces (with reduced title height), placing the title further below center
            * provides more horizontal space and achieves better visual balance.
            */
            if( _title.locY == 0 ) {
                
                // Depending on screen shape we use a different factor to calculate the center of the title
                var centerY = clipHeight * Config.UI_MENU_TITLE_TEXT_POSITION;
                
                // Then we calculate the upper Y coordinate by substracting half of the font height
                // Most fonts have more blank space in the bottom, so for better visual balance we move 
                // the font a bit down, by half the font descent
                var locY = centerY - Graphics.getFontHeight( Config.UI_MENU_TITLE_FONT )/2 + Graphics.getFontDescent( Config.UI_MENU_TITLE_FONT )/2;
                
                _title.setLocation( WatchUi.LAYOUT_HALIGN_CENTER, locY );
            }

            _title.setColor( ThemeManager.current.textColor );

            // Draw the title
            _title.draw( dc );

            // Draw the connection mode indicator
            _cmi.draw( dc );

        } catch( ex ) {
            ExceptionHandler.handleBackgroundException( ex );
        }
    }

    // The `CustomMenu` base class does not expose the number of menu items.
    // To track this, we override the `addItem()` and `deleteItem()` methods.
    private var _itemCount as Number = 0;
    public function addItem( item as CustomMenuItem ) as Void {
        _itemCount++;
        CustomMenu.addItem( item );
    }
    public function deleteItem( index as Number ) as Boolean or Null {
        _itemCount--;
        return CustomMenu.deleteItem( index );
    }
    public function getItemCount() as Number {
        return _itemCount;
    }

    // The settings menu needs a function to focus the first or last item,
    // to support switching between the homepage menu and the parallel settings menu.
    public function focusFirst() as Void {
        setFocus( 0 );
    }
    public function focusLast() as Void {
        setFocus( _itemCount - 1 );
    }
}