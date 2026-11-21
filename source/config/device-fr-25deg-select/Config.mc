import Toybox.Lang;

/*
* Config for the Forerunner series.
*/
class Config extends BaseConfig {
    protected function initialize() { BaseConfig.initialize(); }
    
    // Positions of the keys, for drawing input hints
    // Corresponds to ControlView.InputHints enumeration
    // 0=ENTER
    // 1=BACK
    // 2=UP
    // 3=DOWN
    //
    // The values are angles in degrees.
    public static const UI_INPUT_HINT_POSITIONS as Array<Number> = [25, 330, 180, 210];
}