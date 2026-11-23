import Toybox.Lang;
import Toybox.Application;

/*
 * Exception thrown if the app is started with no sitemap in storage
 * and no connectivity.
 */
class OfflineWithoutSitemapException extends GeneralException {
    function initialize() {
        GeneralException.initialize( Application.loadResource( Rez.Strings.ErrorOfflineNoSitemap ) as String );
    }
}