import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

/*
 * Represents a page defined by a frame widget. Although frames are intended to visually
 * group widgets within a single page, in the context of the Garmin app structure
 * it is more practical to treat them as separate pages.
 *
 * This class builds on the `SitemapContainer` base class, which handles parsing of
 * the widgets contained in the page.
 *
 * It shares this base class with `SitemapPage`. The only difference between a
 * frame and a regular page is the field name used for the title, which is why the
 * title is handled in the subclasses rather than in the base class.
 */
class SitemapFramePage extends SitemapContainer {
    public var title as String;

    // Constructor
    public function initialize( 
        json as JsonAdapter, 
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) {
        SitemapContainer.initialize( json, isSitemapFresh, taskQueue );
        title = parseLabelState(
            json,
            "label",
            Application.loadResource( Rez.Strings.ErrorFrameLabelMissing ) as String
        )[0];
    }
}