import Toybox.Lang;
import Toybox.Application;

/*
 * Exception shown when the app settings have been changed while the app was
 * running.
 *
 * Connect IQ does not provide a way to restart the app, and the singletons
 * have already cached the previous URL, sitemap name, authentication data,
 * polling interval and sitemap. The app therefore stops polling and asks the
 * user to reopen it, so that the new settings are read on the next start.
 */
class SettingsChangedException extends GeneralException {
    function initialize() {
        GeneralException.initialize( Application.loadResource( Rez.Strings.ErrorSettingsChanged ) as String );
    }
}
