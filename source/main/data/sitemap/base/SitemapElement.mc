import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Base class for all sitemap elements, containing members
 * and functions common to both `SitemapWidget` and `SitemapPage`.
 *
 * This class includes only the functionality shared by both,
 * such as the indicator of whether the sitemap is still fresh,
 * and a parser function for widget labels and page titles,
 * which follow the same format.
 */
class SitemapElement {

    protected static const NO_DISPLAY_STATE as String = "—";

    // Separator for the Garmin-specific "Group label|Control label" convention.
    private static const GROUP_LABEL_SEPARATOR as String = "|";

    private var _isSitemapFresh as Boolean;

    // Constructor
    protected function initialize( isSitemapFresh as Boolean ) {
        _isSitemapFresh = isSitemapFresh;
    }

    // Indicates whether the state is fresh
    // If received via web request, it is always considered
    // fresh, if read from storage it depends on the data's age
    public function isSitemapFresh() as Boolean {
        return _isSitemapFresh;
    }

    // Widget labels and page titles may include a display state in the format:
    //   label [state]
    // The display state represents the item's current state with display
    // patterns applied. 
    // This function parses the input into two parts: the label and the state,
    // and returns them as a tuple: [0] = label, [1] = state.
    protected static function parseLabelState( json as JsonAdapter, id as String, errorMessage as String ) as [String, String] {
        return parseLabelStateFromString( json.getString( id, errorMessage ) );
    }

    // Same as parseLabelState(), but for labels that were already read from
    // the JSON. Used for labels and titles that are split before the state
    // is parsed.
    protected static function parseLabelStateFromString( fullLabel as String ) as [String, String] {
        var label = fullLabel;
        var remoteDisplayState = null;
        var bracket = fullLabel.find( " [" ) as Number?;
        if( bracket != null ) {
            label = fullLabel.substring( null, bracket ) as String?;
            if( label == null ) {
                throw new JsonParsingException( Lang.format(
                    Application.loadResource( Rez.Strings.ErrorLabelMissingState ) as String,
                    [ fullLabel ]
                ) );
            }
            remoteDisplayState = fullLabel.substring( bracket+2, fullLabel.length()-1 ) as String?;
        }
        if( remoteDisplayState == null || remoteDisplayState.equals( "-" ) ) {
            remoteDisplayState = NO_DISPLAY_STATE;
        }
        return [label, remoteDisplayState];
    }

    // A Group label may contain a Garmin-specific suffix that defines the
    // label of the derived widget representing the Group item itself:
    //   Group label|Control label [state]
    //
    // The optional state pattern must be at the very end of the label,
    // because that is where openHAB appends the formatted state.
    //
    // The label is only split if both parts are non-empty and the control
    // label does not contain another separator. Otherwise the full label is
    // preserved, so existing labels (including "||") keep their meaning.
    //
    // Returns [groupLabel, controlLabel]. controlLabel is null if the label
    // was not split.
    protected static function splitGroupLabel( fullLabel as String ) as [String, String?] {
        var separator = fullLabel.find( GROUP_LABEL_SEPARATOR ) as Number?;
        if( separator == null || separator == 0 ) {
            return [fullLabel, null as String?];
        }

        var groupLabel = trimSpaces( fullLabel.substring( 0, separator ) as String );
        var controlLabel = trimSpaces( fullLabel.substring( separator + 1, null ) as String );

        if( groupLabel.length() == 0
            || controlLabel.length() == 0
            || controlLabel.find( GROUP_LABEL_SEPARATOR ) != null ) {
            return [fullLabel, null as String?];
        }

        return [groupLabel, controlLabel];
    }

    // Removes leading and trailing spaces. Toybox.Lang.String does not
    // provide a trim() method.
    private static function trimSpaces( value as String ) as String {
        var start = 0;
        var end = value.length();
        while( start < end ) {
            var char = value.substring( start, start + 1 ) as String;
            if( ! char.equals( " " ) ) {
                break;
            }
            start++;
        }
        while( end > start ) {
            var lastChar = value.substring( end - 1, end ) as String;
            if( ! lastChar.equals( " " ) ) {
                break;
            }
            end--;
        }
        return value.substring( start, end ) as String;
    }
}