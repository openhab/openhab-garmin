import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;
import Toybox.System;

/*
 * The settings menu provides access to the app and server configuration,
 * connection status, and maintenance actions.
 * In the future, additional functionality may be added, such as switching between servers.
 */
class SettingsMenu extends BaseMenu {

    public function initialize() {
        // Initialize the base class
        BaseMenu.initialize( {
            :title => Application.loadResource( Rez.Strings.Settings ) as String,
            :itemHeight => Config.UI_SETTINGS_ITEM_HEIGHT
        } );

        // Add entry for app version
        addItem( new LabelMenuItem(
            Application.loadResource( Rez.Strings.SettingsAppVersion ) as String,
            Application.loadResource( Rez.Strings.AppVersion ) as String
        ) );

        // Add entry for server URL
        addItem( new LabelMenuItem(
            Application.loadResource( Rez.Strings.SettingsServer ) as String,
            AppSettings.getUrl()
        ) );

        // Add entry for sitemap name
        addItem( new LabelMenuItem(
            Application.loadResource( Rez.Strings.SettingsSitemap ) as String,
            AppSettings.getSitemap()
        ) );

        // Add entry for the Connect IQ version
        addItem( new LabelMenuItem(
            Application.loadResource( Rez.Strings.SettingsConnectIqVersion ) as String,
            Lang.format( "$1$.$2$.$3$", System.getDeviceSettings().monkeyVersion )
        ) );

        // Add entry for connectivity mode
        addItem( new ConnectionModeMenuItem() );
        
        // Add entry for sitemap age
        addItem( new SitemapLastUpdatedMenuItem() );
        
        // Add entry for Wi-Fi sitemap refresh
        addItem( new SitemapRefreshMenuItem() );
    }
}