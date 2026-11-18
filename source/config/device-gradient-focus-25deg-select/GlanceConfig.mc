import Toybox.Lang;

/*
* `GlanceConfig` for devices whose native menu uses a gradient background
* as the selected-item indicator.
* No changes to the default values are required.
*/
(:glance)
class GlanceConfig extends BaseGlanceConfig {
    protected function initialize() { BaseGlanceConfig.initialize(); }
}
