import Toybox.Lang;

/*
 * Config for devices whose native menu uses a gradient background
 * as the selected-item indicator, and whose select button is positioned
 * at 27 degrees.
 */
class Config extends BaseConfig {
    protected function initialize() { BaseConfig.initialize(); }

    // No left padding, see ../device-gradient-focus/Config.mc
    public static const UI_MENU_ITEM_PADDING_LEFT_FACTOR as Float = 0.0;

    // Positions of the keys, for drawing input hints
    // Corresponds to ControlView.InputHints enumeration
    // 0=ENTER
    // 1=BACK
    public static const UI_INPUT_HINT_POSITIONS as Array<Number> = [27, 330, 180, 210];
}
