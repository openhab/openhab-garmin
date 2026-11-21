import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Singleton class that provides three BufferedBitmaps representing
 * a toggle switch in the "on" and "off" positions, and without a state.
 *
 * `OnOffStateDrawable` provides the standard-sized version,
 * while this class offers a smaller version intended for use 
 * alongside a submenu indicator.
 */
class SmallOnOffStateBitmaps extends OnOffStateBitmaps {
    
    // When combined with a sub-menu indicator, the toggle switch will be rendered smaller 
    // The factors below are 0.65 the factor of the standard sized icon
    public var HEIGHT as Number = ( Config.UI_MENU_ITEM_HEIGHT * 0.52 ).toNumber(); // Factor 0.8 * 0.65
    public var WIDTH as Number = ( Config.UI_MENU_ITEM_HEIGHT * 0.2925 ).toNumber(); // Factor 0.45 * 0.65

    // Constructor
    public function initialize( 
        onFillColor as ColorType, 
        offFillColor as ColorType,
        switchColor as ColorType 
    ) {
        OnOffStateBitmaps.initialize( onFillColor, offFillColor, switchColor );
    }

}