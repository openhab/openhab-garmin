import Toybox.Lang;
import Toybox.Graphics;
import Rez.Styles;

/*
 * Themes define the color schemes used in the app and can
 * change dynamically at runtime. This also includes icons
 * whose colors must be adapted to the active theme.
 *
 * The dark theme is the default on devices that do not support
 * night mode (currently all watches). On devices that support
 * night mode (currently all Edge cycling computers), it is
 * used when night mode is active.
 */
class DefaultTheme extends BaseTheme {

    public var textColor as ColorType = Graphics.COLOR_WHITE;

    public var stateColor as ColorType = Graphics.COLOR_LT_GRAY;

    public var backgroundColor as ColorType = Graphics.COLOR_BLACK;

    public var menuTitleBackgroundColor as ColorType = 0x212021;
    
    // The dividers are not used on devices that use the default theme.
    public var menuTitleDividerColor as ColorType = Graphics.COLOR_TRANSPARENT;
    public var menuItemDividerColor as ColorType = Graphics.COLOR_TRANSPARENT;

    public var logoOpenhabText as ResourceId = Rez.Drawables.logoOpenhabTextWhite;

    public var menuSettings as ResourceId = Rez.Drawables.menuSettingsLightGray;

    public var iconHourglass as ResourceId = Rez.Drawables.iconHourglassWhite;

    public var iconPlayButton as ResourceId = Rez.Drawables.iconPlayButtonLightGrey;
    public var iconPauseButton as ResourceId = Rez.Drawables.iconPauseButtonLightGrey;
    public var iconPlayMenuItem as ResourceId = Rez.Drawables.iconPlayMenuItemLightGrey;
    public var iconPauseMenuItem as ResourceId = Rez.Drawables.iconPauseMenuItemLightGrey;

    public var iconNextHint as ResourceId = Rez.Drawables.iconNextHintWhite;
    public var iconPreviousHint as ResourceId = Rez.Drawables.iconPreviousHintWhite;
    public var iconNext as ResourceId = Rez.Drawables.iconNextWhite;
    public var iconPrevious as ResourceId = Rez.Drawables.iconPreviousWhite;

    public var onOffBitmaps as OnOffStateBitmaps = 
        OnOffStateBitmaps.getShared( onColor, stateColor, backgroundColor );
    public var smallOnOffBitmaps as SmallOnOffStateBitmaps = 
        SmallOnOffStateBitmaps.getSharedSmall( onColor, stateColor, backgroundColor );

    public function initialize() {
        BaseTheme.initialize();
    }
}