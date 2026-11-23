import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

/*
 * Represents the submenu content of a Group element.
 *
 * In addition to the group members provided by the server, this page contains
 * a derived widget that represents the Group item itself. This allows the user
 * to control the group as a whole (e.g., a master switch) without leaving the
 * submenu. The derived widget is created from the Group element without its
 * `linkedPage`, so selecting it sends a command instead of navigating.
 *
 * A Group label may contain a Garmin-specific suffix defining the label of the
 * derived widget: "Group label|Control label [state]". The optional state
 * pattern must be at the end of the label, because that is where openHAB
 * appends the formatted state. See SitemapElement.splitGroupLabel().
 */
class SitemapGroupPage extends SitemapPage {

    public function initialize(
        json as JsonAdapter,
        groupElement as JsonAdapter,
        controlLabel as String?,
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) {
        SitemapPage.initialize( json, isSitemapFresh, taskQueue );

        // If the group label contains a control label, the server-provided
        // page title also contains it, because openHAB derives the title from
        // the widget label. Strip it so that the submenu header only shows the
        // group label.
        if( controlLabel != null ) {
            var rawTitle = json.getString(
                "title",
                Application.loadResource( Rez.Strings.ErrorPageTitleMissing ) as String
            );
            title = parseLabelStateFromString( splitGroupLabel( rawTitle )[0] )[0];
        }

        var control = SitemapWidgetFactory.createGroupControl(
            groupElement,
            controlLabel,
            isSitemapFresh,
            taskQueue
        );
        if( control != null ) {
            // The child build tasks are only queued at this point, so adding
            // the derived widget here puts it at index 0 before they run.
            addWidget( control );
        }
    }
}
