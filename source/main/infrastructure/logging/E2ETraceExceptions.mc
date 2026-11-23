import Toybox.Lang;

/*
 * Maps exceptions to the stable identifiers and codes used in E2E trace
 * events. See `E2ETrace` for the event format.
 *
 * The mapping intentionally does not use localized messages, so E2E
 * assertions do not depend on the display language. Only debug builds
 * include the mapping; it is referenced exclusively from (:debug) helpers.
 */
class E2ETraceExceptions {

    // Stable identifier for an exception, e.g. "communication.sitemap".
    (:debug)
    public static function identifier( ex as Exception ) as String {
        if( ex instanceof CommunicationException ) {
            return "communication." + sourceSuffix( ex );
        }
        if( ex instanceof UnexpectedResponseException ) {
            return "unexpected-response." + sourceSuffix( ex );
        }
        if( ex instanceof JsonParsingException ) {
            return "json-parsing";
        }
        if( ex instanceof ConfigException ) {
            return "config";
        }
        if( ex instanceof OutOfMemoryException ) {
            return "out-of-memory";
        }
        if( ex instanceof OfflineCommandException ) {
            return "offline-command";
        }
        if( ex instanceof OfflineWithoutSitemapException ) {
            return "offline-without-sitemap";
        }
        if( ex instanceof SettingsChangedException ) {
            return "settings-changed";
        }
        if( ex instanceof FatalUserInterfaceException ) {
            return "fatal-ui";
        }
        if( ex instanceof NonFatalUserInterfaceException ) {
            return "nonfatal-ui";
        }
        if( ex instanceof GeneralException ) {
            return "general";
        }
        return "exception";
    }

    // Exception-specific code: the communication response code where there is
    // one, the UI error code for non-fatal UI errors, otherwise "-".
    (:debug)
    public static function code( ex as Exception ) as String {
        if( ex instanceof CommunicationException ) {
            return ex.getResponseCode().toString();
        }
        // OfflineCommandException inherits the non-fatal UI type but overrides
        // its toast message with a localized text, so no numeric code exists.
        if( ex instanceof OfflineCommandException ) {
            return "-";
        }
        if( ex instanceof NonFatalUserInterfaceException ) {
            return ex.getToastMessage();
        }
        return "-";
    }

    /*
     * Stable, language-independent identifier used by toast and error-view
     * events. Communication errors carry their source prefix (S for sitemap,
     * C for command); UI errors carry a UI prefix. No translated text is used.
     */
    (:debug)
    public static function errorId( ex as Exception ) as String {
        if( ex instanceof CommunicationException ) {
            return ( ex.isFrom( CommunicationBaseException.EX_SOURCE_SITEMAP ) ? "S:" : "C:" )
                + ex.getResponseCode().toString();
        }
        if( ex instanceof UnexpectedResponseException ) {
            return ( ex.isFrom( CommunicationBaseException.EX_SOURCE_SITEMAP ) ? "S:" : "C:" )
                + "RESPONSE";
        }
        // OfflineCommandException always uses EX_OFFLINE (UI:03) but overrides
        // getToastMessage() with a localized text.
        if( ex instanceof OfflineCommandException ) {
            return "UI:03";
        }
        if( ex instanceof NonFatalUserInterfaceException ) {
            return ex.getToastMessage();
        }
        if( ex instanceof FatalUserInterfaceException ) {
            return "UI:FATAL";
        }
        if( ex instanceof SettingsChangedException ) {
            return "UI:SETTINGS";
        }
        if( ex instanceof ConfigException ) {
            return "UI:CONFIG";
        }
        if( ex instanceof JsonParsingException ) {
            return "S:JSON";
        }
        if( ex instanceof OutOfMemoryException ) {
            return "S:OOM";
        }
        if( ex instanceof OfflineWithoutSitemapException ) {
            return "S:OFFLINE";
        }
        if( ex instanceof GeneralException ) {
            return "UI:GENERAL";
        }
        return "UI:UNKNOWN";
    }

    // Source of the error: sitemap, command or ui.
    (:debug)
    public static function source( ex as Exception ) as String {
        if( ex instanceof CommunicationBaseException ) {
            return ex.isFrom( CommunicationBaseException.EX_SOURCE_SITEMAP )
                ? "sitemap"
                : "command";
        }
        if( ex instanceof JsonParsingException
            || ex instanceof OutOfMemoryException
            || ex instanceof OfflineWithoutSitemapException
        ) {
            return "sitemap";
        }
        return "ui";
    }

    (:debug)
    private static function sourceSuffix( ex as CommunicationBaseException ) as String {
        return ex.isFrom( CommunicationBaseException.EX_SOURCE_SITEMAP )
            ? "sitemap"
            : "command";
    }
}
