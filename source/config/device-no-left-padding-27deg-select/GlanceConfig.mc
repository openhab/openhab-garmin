import Toybox.Lang;

/*
* `GlanceConfig` for devices that render the menu item content at the left edge.
* No changes to the default values are required.
*/
(:glance)
class GlanceConfig extends BaseGlanceConfig {
    protected function initialize() { BaseGlanceConfig.initialize(); }
}
