import Toybox.Lang;
import Toybox.Graphics;

/*
 * Themes define the color schemes used in the app and can
 * change dynamically at runtime. This also includes icons
 * whose colors must be adapted to the active theme.
 *
 * The light theme is the default on devices that support
 * night mode (currently all Edge cycling computers).
 * It is used when night mode is not active.
 */
class LightTheme extends BaseTheme {

    public var textColor as ColorType = Graphics.COLOR_BLACK;

    public var stateColor as ColorType = Graphics.COLOR_DK_GRAY;

    public var backgroundColor as ColorType = Graphics.COLOR_WHITE;

    public var menuTitleBackgroundColor as ColorType = 0xEEEAEE;
    public var menuTitleDividerColor as ColorType = 0xC5C2C5;
    public var menuItemDividerColor as ColorType = 0xDEDFDE;
    public var menuItemBackgroundColor as ColorType = backgroundColor;

    public var logoOpenhabText as ResourceId = Rez.Drawables.logoOpenhabTextBlack;

    public var menuSettings as ResourceId = Rez.Drawables.menuSettingsDarkGray;

    public var iconHourglass as ResourceId = Rez.Drawables.iconHourglassBlack;

    public var iconPlayButton as ResourceId = Rez.Drawables.iconPlayButtonDarkGrey;
    public var iconPauseButton as ResourceId = Rez.Drawables.iconPauseButtonDarkGrey;
    public var iconPlayMenuItem as ResourceId = Rez.Drawables.iconPlayMenuItemDarkGrey;
    public var iconPauseMenuItem as ResourceId = Rez.Drawables.iconPauseMenuItemDarkGrey;

    public var iconNextHint as ResourceId = Rez.Drawables.iconNextHintBlack;
    public var iconPreviousHint as ResourceId = Rez.Drawables.iconPreviousHintBlack;
    public var iconNext as ResourceId = Rez.Drawables.iconNextBlack;
    public var iconPrevious as ResourceId = Rez.Drawables.iconPreviousBlack;

    public var onOffBitmaps as OnOffStateBitmaps = 
        OnOffStateBitmaps.getShared( onColor, stateColor, backgroundColor );
    public var smallOnOffBitmaps as SmallOnOffStateBitmaps = 
        SmallOnOffStateBitmaps.getSharedSmall( onColor, stateColor, backgroundColor );

    public function initialize() {
        BaseTheme.initialize();
    }

}