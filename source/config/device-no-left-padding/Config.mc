import Toybox.Lang;

/*
 * Config for devices that render the menu item content at the left edge
 * of the menu item.
 *
 * This applies to the Fenix models starting with the Fenix 8, and to the
 * Forerunner x65 models and newer.
 *
 * These devices indicate the selected item with a background highlight
 * instead of a bar on the left side of the screen, so no space needs to
 * be reserved between the left edge and the menu item content.
 */
class Config extends BaseConfig {
    protected function initialize() { BaseConfig.initialize(); }

    public static const UI_MENU_ITEM_PADDING_LEFT_FACTOR as Float = 0.0;
}
