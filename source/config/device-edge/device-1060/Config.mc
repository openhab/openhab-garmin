import Toybox.Lang;
import Toybox.Graphics;
import Toybox.System;

/*
* Config specific to the Edge 1060.
*
* The Edge 1060 is controlled by buttons as well as a touch screen and,
* like the button-based Edge x50 devices, is navigated using buttons.
* It therefore shares their configuration.
*
* The workaround for the faulty focus indicator that is required on the
* Edge 550 and 850 (see ../device-x50-button/device-550/Config.mc)
* is not enabled here. If the focus indicator turns out to be affected
* on the Edge 1060 as well, the corresponding constants can be overridden.
*/
class Config extends EdgeX50ButtonConfig {
    protected function initialize() { EdgeX50ButtonConfig.initialize(); }

    // Note: Static functions cannot access inherited static constants.
    // Therefore, any constant that is required in a static function
    // must be explicitly defined in each Config implementation.
    public static const UI_MENU_FOCUS_EDGEX50_SUPRESS_FOCUS_INDICATOR as Boolean = false;
}
