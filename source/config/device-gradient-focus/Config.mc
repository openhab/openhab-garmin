import Toybox.Lang;

/*
 * Config for devices whose native menu uses a gradient background
 * as the selected-item indicator.
 *
 * This applies to the Fenix models starting with the Fenix 8, and to the
 * Forerunner x65 models and newer.
 *
 * On these devices the native menu does not reserve space to the left of
 * the selected item, so the menu item content is aligned to the left edge.
 * The left padding is therefore removed to achieve the same appearance.
 */
class Config extends BaseConfig {
    protected function initialize() { BaseConfig.initialize(); }

    public static const UI_MENU_ITEM_PADDING_LEFT_FACTOR as Float = 0.0;
}
