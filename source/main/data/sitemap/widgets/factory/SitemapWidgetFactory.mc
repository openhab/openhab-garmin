import Toybox.Lang;
import Toybox.Application;
import Toybox.WatchUi;

/*
 * Factory class for creating the appropriate object 
 * based on a given sitemap element.
 */
class SitemapWidgetFactory {

    // Create an element for a given type
    public static function createByType( 
        widget as JsonAdapter, 
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) as SitemapWidget {

        // Determine the widget type
        var type = widget.getOptionalString( "type" );
        if( type.equals( "" ) ) {
            var label = widget.getOptionalString( "label" );
            throw new JsonParsingException(
                label.equals( "" )
                ? Application.loadResource( Rez.Strings.ErrorWidgetWithoutType ) as String
                : Lang.format(
                    Application.loadResource( Rez.Strings.ErrorWidgetWithoutTypeLabel ) as String,
                    [ label ]
                )
            );
        }

        // Based on the groupType of the Group item, the Group sitemap element
        // is either mapped to a type-specific widget (for consistent presentation)
        // or falls back to a generic Group widget if no specific type is supported.
        if( type.equals( "Group" ) ) {
            var groupWidgetType = getGroupWidgetType( widget );
            if( groupWidgetType != null ) {
                type = groupWidgetType;
            }
        }

        // Now we create the widget object based on the type
        if( type.equals( "Switch" ) || type.equals( "Selection" ) ) {
            return new SitemapSwitch( widget, isSitemapFresh, taskQueue );
        } else if( type.equals( "Setpoint" ) || type.equals( "Slider" ) ) {
            return new SitemapNumeric( widget, isSitemapFresh, taskQueue );
        } else if( type.equals( "Text" ) ) {
            return new SitemapText( widget, isSitemapFresh, taskQueue );
        } else if( type.equals( "Group" ) ) {
            return new SitemapGroup( widget, isSitemapFresh, taskQueue );
        } else if( type.equals( "Frame" ) ) {
            return new SitemapFrame( widget, isSitemapFresh, taskQueue );
        } else {
            var label = widget.getOptionalString( "label" );
            throw new JsonParsingException(
                label.equals( "" )
                ? Lang.format(
                    Application.loadResource( Rez.Strings.ErrorUnsupportedElementType ) as String,
                    [ type ]
                )
                : Lang.format(
                    Application.loadResource( Rez.Strings.ErrorUnsupportedElementTypeForElement ) as String,
                    [ label, type ]
                )
            );
        }
    }

    // Returns true if the given Group element maps to a widget that can
    // send commands to the Group item.
    public static function isControllableGroup( groupElement as JsonAdapter ) as Boolean {
        return getGroupWidgetType( groupElement ) != null;
    }

    // Creates a widget representing the Group item itself. The widget is
    // derived from the Group element and does not contain its `linkedPage`,
    // so selecting it sends a command instead of opening the Group submenu.
    // Returns null for group types that have no actionable widget.
    public static function createGroupControl(
        groupElement as JsonAdapter,
        controlLabel as String?,
        isSitemapFresh as Boolean,
        taskQueue as TaskQueue
    ) as SitemapWidget? {
        if( ! isControllableGroup( groupElement ) ) {
            return null;
        }

        var controlJson = groupElement.copyWithout( "linkedPage" );
        if( controlLabel != null ) {
            controlJson.setString( "label", controlLabel );
        }
        return createByType( controlJson, isSitemapFresh, taskQueue );
    }

    // Maps the groupType of a Group item to the sitemap widget type used
    // to represent it. Returns null if no type-specific widget is supported.
    private static function getGroupWidgetType( groupElement as JsonAdapter ) as String? {
        var jsonItem = groupElement.getOptionalObject( "item" );
        if( jsonItem == null ) {
            var label = groupElement.getOptionalString( "label" );
            throw new JsonParsingException(
                label.equals( "" )
                ? Application.loadResource( Rez.Strings.ErrorGroupWithoutItem ) as String
                : Lang.format(
                    Application.loadResource( Rez.Strings.ErrorGroupWithoutItemLabel ) as String,
                    [ label ]
                )
            );
        }
        var groupType = jsonItem.getOptionalString( "groupType" );
        switch( groupType ) {
            case "Switch": return "Switch";
            case "Rollershutter": return "Switch";
            case "Dimmer": return "Slider";
            default: return null;
        }
    }
}