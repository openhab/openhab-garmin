import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Provides three BufferedBitmaps representing a toggle switch in the
 * "on" and "off" positions, and without a state.
 *
 * `OnOffStateDrawable` provides the standard-sized version,
 * while this class offers a smaller version intended for use 
 * alongside a submenu indicator.
 *
 * See OnOffStateBitmaps for details about creation and sharing.
 */
class SmallOnOffStateBitmaps extends OnOffStateBitmaps {
    
    // When combined with a sub-menu indicator, the toggle switch will be rendered smaller 
    // The factors below are 0.65 the factor of the standard sized icon
    public var HEIGHT as Number = ( Config.UI_MENU_ITEM_HEIGHT * 0.52 ).toNumber(); // Factor 0.8 * 0.65
    public var WIDTH as Number = ( Config.UI_MENU_ITEM_HEIGHT * 0.2925 ).toNumber(); // Factor 0.45 * 0.65

    // Instances that are shared between themes
    private static var _sharedSmall as Array<SmallOnOffStateBitmaps> = [];

    /*
     * Returns the instance for the given colors, reusing an existing instance
     * if another theme was already created with the same colors.
     * See OnOffStateBitmaps.getShared().
     */
    public static function getSharedSmall(
        onFillColor as ColorType,
        offFillColor as ColorType,
        switchColor as ColorType
    ) as SmallOnOffStateBitmaps {
        for( var i = 0; i < _sharedSmall.size(); i++ ) {
            if( _sharedSmall[i].hasColors( onFillColor, offFillColor, switchColor ) ) {
                return _sharedSmall[i];
            }
        }
        var bitmaps = new SmallOnOffStateBitmaps( onFillColor, offFillColor, switchColor );
        _sharedSmall.add( bitmaps );
        return bitmaps;
    }

    // Constructor
    public function initialize( 
        onFillColor as ColorType, 
        offFillColor as ColorType,
        switchColor as ColorType 
    ) {
        OnOffStateBitmaps.initialize( onFillColor, offFillColor, switchColor );
    }

}