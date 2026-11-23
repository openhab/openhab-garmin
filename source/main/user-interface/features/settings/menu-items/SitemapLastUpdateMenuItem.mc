import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Graphics;
import Toybox.Time;
using Toybox.Time.Gregorian;

/*
 * A custom menu item that displays the timestamp of the currently available
 * sitemap data.
 */
class SitemapLastUpdatedMenuItem extends LabelMenuItem {

    /*
    * Constructor.
    * Initializes the base class with the label and sublabel.
    */
    public function initialize() {
        LabelMenuItem.initialize( 
            Application.loadResource( Rez.Strings.SettingsLastSitemapUpdate ) as String,
            getSitemapTimestamp()
        );
    }

    // Returns a formatted version of the current sitemap age
    private function getSitemapTimestamp() as String {
        if( ConnectionManager.get().isBluetoothConnected() ) {
            return Application.loadResource( Rez.Strings.SitemapLastUpdateJustNow ) as String;
        } else {
            var timestamp = SitemapStore.getSitemapTimestamp();
            if( timestamp != null ) {
                var today = Gregorian.info( timestamp, Time.FORMAT_MEDIUM );
                return Lang.format(
                    "$4$ $5$ $6$ $7$ $1$:$2$:$3$",
                    [
                        TimeFormatting.twoDigits( today.hour ),
                        TimeFormatting.twoDigits( today.min ),
                        TimeFormatting.twoDigits( today.sec ),
                        today.day_of_week,
                        today.day,
                        today.month,
                        today.year
                    ]
                );
            } else {
                return Application.loadResource( Rez.Strings.SitemapLastUpdateUnknown ) as String;
            }
        }
    }

    // Returns a formatted version of the current sitemap age
    /*
    private function getSitemapAge() as String {
        var duration = SitemapStore.getSitemapAge();
        if( duration != null ) {
            if( duration.value() > 59 ) {
                return TimeFormatting.formatDuration( duration, 4 );
            } else {
                return "< 1 min";
            }
        } else {
            return "n/a";
        }
    }
    */

    /*
    * Called by the base class to handle drawing.
    * Also updates the sitemap age.
    */
    public function onUpdate( dc as Dc ) as Void {
        setSubLabel( getSitemapTimestamp() );
        LabelMenuItem.onUpdate( dc );
    }
}