import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Base class for all widget elements. Contains members and functions
 * common to all widgets, including:
 * - the label
 * - The display state: the item's state with display patterns applied.
 *   - `_remoteDisplayState` holds the display state as provided by the server.
 *   - `_displayState` may override `_remoteDisplayState` with additional local display logic.
 * - the display colors
 * - any page linked to this widget
 */
class SitemapWidget extends SitemapElement {

    // See the get accessors for documentation
    private var _commandConfirmMessage as String;
    private var _displayState as String;
    private var _icon as ResourceId?;
    private var _iconType as String;
    private var _staticIcon as Boolean;
    private var _item as Item?;
    private var _label as String;
    private var _labelColor as ColorType?;
    private var _linkedPage as SitemapContainer?;
    private var _remoteDisplayState as String;
    private var _type as String;
    private var _valueColor as ColorType?;

    // Constructor
    // @param json - The JSON object representing this widget.
    // @param item - The associated item. Not all subclasses require one,
    //               and the item type may vary by widget. The subclass
    //               creates and passes in the item so it can be accessed
    //               through the generic SitemapWidget interface.
    // @param linkedPage - By default, this class parses the `linkedPage`
    //                     from the JSON. For special cases like Frame elements,
    //                     the `linkedPage` is passed explicitly to override
    //                     the default behavior.
    // @param isSitemapFresh - Indicates whether the sitemap is fresh
    //                         (i.e., within its expiry period and containing up-to-date state).
    // @param taskQueue - The task queue to be used. Recursive structures are traversed
    //                    iteratively to avoid stack overflows and unresponsive UI.
    //                    See the task queue classes for details.
    protected function initialize( 
        json as JsonAdapter, 
        item as Item?,
        linkedPage as SitemapContainer?,
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) {
        SitemapElement.initialize( isSitemapFresh );

        _item = item;

        _type = json.getString( "type", "Widget without type" );

        var fullLabel = parseLabelState( json, "label", "Widget label is missing" );
        _label = fullLabel[0];

        // We fill the states only if the sitemap is fresh,
        // otherwise they are set to NO_DISPLAY_STATE
        if( isSitemapFresh ) {
            _remoteDisplayState = fullLabel[1];
            
            // display state is filled by either remote
            // display state or the item state
            _displayState = 
                ! _remoteDisplayState.equals( NO_DISPLAY_STATE )
                    ? _remoteDisplayState
                    : item != null && item.hasState()
                        ? item.getState()
                        : NO_DISPLAY_STATE;
        } else {
            _remoteDisplayState = NO_DISPLAY_STATE;
            _displayState = NO_DISPLAY_STATE;
        }
        
        // If staticIcon is true, we do not use the
        // item state when selecting an icon but
        // use the default presentation of the
        // dynamic icons 
        _iconType = json.getOptionalString( "icon" );
        _staticIcon = json.getBoolean( "staticIcon" );

        // The confirmation message to display before a command is sent.
        // If absent, null or an empty string, no confirmation is required
        // and commands are issued immediately (see CommandMenuItem).
        // The value is resolved by the server and may change dynamically.
        _commandConfirmMessage = json.getOptionalString( "commandConfirmMessage" );

        _icon = getCurrentIcon();

        _labelColor = ColorParser.parse( json, "labelcolor", "Widget '" + _label + "': invalid label color" );
        _valueColor = ColorParser.parse( json, "valuecolor", "Widget '" + _label + "': invalid value color" );

        if( linkedPage != null ) {
            _linkedPage = linkedPage;
        } else {
            var jsonLinkedPage = json.getOptionalObject( "linkedPage" );
            if( jsonLinkedPage != null ) {
                _linkedPage = new SitemapPage( jsonLinkedPage, isSitemapFresh, taskQueue );
            }
        }
    }

    // Returns the current icon, based on the icon type
    // and the item state if the icon is not static
    private function getCurrentIcon() as ResourceId? {
        return IconParser.parse( 
            _iconType, 
            _staticIcon
                ? null
                : _item           
        );
    }

    // The display state is initialized with the remote display state; 
    // subclasses may override it with custom display logic.
    public function getDisplayState() as String { return _displayState; }

    // As default the display state is set to NO_DISPLAY_STATE if it
    // is not available. This function can be used if absence of a
    // display state should be expressed as null.
    public function getDisplayStateOrNull() as String? { 
        if( _displayState.equals( NO_DISPLAY_STATE ) ) {
            return null;
        } else {
            return _displayState; 
        }
    }

    // The _iconType transformed into a bitmap resource id
    public function getIcon() as ResourceId? { return _icon; }
    
    // The icon type as specified in the sitemap
    public function getIconType() as String { return _iconType; }
    
    // The item associated with this widget
    public function getItem() as Item? { return _item; }
    
    // The label, without any embedded display state
    // See _remoteDisplayState and SitemapElement.parseLabel()
    public function getLabel() as String { return _label; }

    // The color to be applied to the label
    public function getLabelColor() as ColorType? { return _labelColor; }

    // The confirmation message to be shown before a command is issued.
    // An empty string means that no confirmation is required and the
    // command is sent immediately.
    public function getCommandConfirmMessage() as String { return _commandConfirmMessage; }
    
    // For Group elements and nested elements
    public function getLinkedPage() as SitemapContainer? { return _linkedPage; }
    
    // The display state provided by the server.
    // Extracted from the item label in the format: "Label [_displayState]"
    // See SitemapElement.parseLabel()
    public function getRemoteDisplayState() as String { return _remoteDisplayState; }

    // As default the remote display state is set to NO_DISPLAY_STATE if it
    // is not available. This function can be used if absence of a remote
    // display state should be expressed as null.
    public function getRemoteDisplayStateOrNull() as String? { 
        if( _remoteDisplayState.equals( NO_DISPLAY_STATE ) ) {
            return null;
        } else {
            return _remoteDisplayState; 
        }
    }

    // The widget type
    public function getType() as String { return _type; }

    // The value color is applied to the displayed state
    public function getValueColor() as ColorType? { return _valueColor; }

    // Determines if a remote display state is available
    public function hasRemoteDisplayState() as Boolean {
        return ! _remoteDisplayState.equals( NO_DISPLAY_STATE );
    }

    // Determines if a display state is available
    public function hasDisplayState() as Boolean {
        return ! _displayState.equals( NO_DISPLAY_STATE );
    }

    // To be used to update the state if a change
    // is triggered from within the app ("internal update")
    public function updateState( state as Item.ItemState ) as Void {
        _remoteDisplayState = NO_DISPLAY_STATE;
        if( _item != null ) {
            var item = _item;
            item.updateState( state );
            _displayState = item.getState();
        } else {
            _displayState = NO_DISPLAY_STATE;
        }
        // Needs to be called after we updated the item
        _icon = getCurrentIcon();
    }
}