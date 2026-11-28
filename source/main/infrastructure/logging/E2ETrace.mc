import Toybox.Lang;
import Toybox.System;

/*
 * Debug-only trace events for end-to-end tests.
 *
 * E2E tests drive the app in the simulator and assert on machine-readable
 * events instead of relying on screenshots alone. Every event is printed as
 * exactly one line:
 *
 *     E2E:<event>;<field>=<value>[;<field>=<value>...]
 *
 * The prefix, event names and field names are stable. Fields are always
 * emitted in the order documented below.
 *
 * Events:
 *
 *   E2E:VIEW;category=<category>;view=<view>;depth=<n>
 *       category: sitemap | settings | error | sync | glance
 *       view:     stable view name, e.g. HomepageMenu, SettingsMenu, ErrorView
 *       depth:    app view-stack depth (glance reports 1; the system sync
 *                 screen reports the depth of the app stack it overlays; the
 *                 command ActionMenu reports the stack depth + 1)
 *
 *   E2E:CONNECTION;mode=<mode>
 *       mode: BLE | WIFI_CHECK_PENDING | WIFI | OFFLINE
 *       Emitted on startup and on every connection mode change.
 *
 *   E2E:SITEMAP;result=<result>;code=<code>;bytes=<bytes>
 *       result: success | unchanged | error
 *       code:   HTTP response code or SDK error code, or "-" if unavailable
 *       bytes:  estimated sitemap size in bytes, or "-" if unavailable
 *
 *   E2E:COMMAND;result=<result>;code=<code>;item=<item>;value=<command>
 *       result: success | error
 *       code:   HTTP response code or SDK error code, or "-" if unavailable
 *       item:   item name, percent-encoded (spaces, "|" and "%")
 *       value:  command value, percent-encoded (spaces, "|" and "%")
 *
 *   E2E:INPUT;button=<button>;view=<view>;depth=<n>
 *       button: select | back | next-page | previous-page | wrap | footer
 *       view:   stable name of the view that received the input
 *       depth:  app view-stack depth
 *       Emitted by the menu input delegate when a press reaches the app. A
 *       press the simulator never delivered emits nothing, which is the
 *       difference a screenshot cannot make. Scrolling between entries is
 *       handled inside Menu2 and never reaches the delegate, so there is no
 *       per-entry event: a Down that moves the highlight is observable only as
 *       a repaint (the runner measures it). The page/wrap buttons fire when the
 *       highlight runs past the first or last entry.
 *
 *   E2E:SELECT;kind=<kind>;result=<result>;depth=<n>
 *       kind:   sitemap element type, e.g. Frame, Group, Switch, Text
 *       result: pushed | empty-page | page-not-ready | no-action | offline |
 *               command
 *       depth:  app view-stack depth
 *       Emitted when a menu item handled a select, and says what it decided.
 *       Without it, "the item opened nothing" is indistinguishable from "the
 *       press never arrived".
 *
 *   E2E:FOCUS;view=<view>;depth=<n>;index=<n>;kind=<kind>;label=<label>
 *       view:  stable name of the menu that owns the focused entry
 *       depth: app view-stack depth
 *       index: position of the entry in that menu, counted from 0
 *       kind:  sitemap element type (Frame, Group, Switch, Text, ...) for a
 *              sitemap entry; Settings, Loading or MenuItem for the entries
 *              the app adds itself
 *       label: the entry's sitemap label, percent-encoded like COMMAND.item.
 *              Empty for an entry whose label is translated chrome: the trace
 *              never carries translated free text.
 *       Emitted when the focused entry changes, i.e. after a highlight moved
 *       and when a page is pushed or popped, because the entry that becomes
 *       focused is then drawn for the first time. A repeat of the same
 *       (view, depth, index, kind, label) is suppressed, so a polling app's
 *       repaints do not turn the trace into a per-frame flood. Scrolling
 *       between entries is handled inside Menu2 and never reaches the input
 *       delegate; this is the only event that names the row a press landed on,
 *       which is what lets a scenario address an element by label instead of
 *       by a hard-coded press count.
 *
 *   E2E:ERROR;kind=<kind>;id=<id>;code=<code>;source=<source>
 *       kind:   temporary | critical | fatal
 *       id:     stable exception identifier, e.g. communication.sitemap
 *       code:   exception-specific code, or "-" if unavailable
 *       source: sitemap | command | ui
 *
 *   E2E:TOAST;id=<id>
 *       id: stable code or resource identifier, never translated free text;
 *           always the last field
 *
 *   E2E:ERRORVIEW;id=<id>;code=<code>
 *       id:   stable error code, e.g. S:-104 or UI:CONFIG
 *       code: exception-specific code, or "-" if unavailable
 *
 *   E2E:LANGUAGE;code=<code>
 *       code: active system language, e.g. eng, deu, or unknown
 *
 * The error classification is the one the app actually applies:
 * - temporary: reported as a toast, the app continues polling or running
 * - critical: reported as a full-screen error, the app continues running
 * - fatal: requires an app restart, polling is latched off
 *
 * All output is compiled only into (:debug) builds. The (:release)
 * implementations are empty and contain no event text, so release builds
 * neither print nor retain any E2E data.
 *
 * E2E is test-only instrumentation: events must never contain credentials,
 * full URLs with authentication data, or translated free text. Item names,
 * command values and sitemap labels are the only server-sourced values, and
 * they are required by the event schemas that carry them; chrome text is never
 * reported, which is why FOCUS leaves the label empty for a non-sitemap entry.
 */
(:glance)
class E2ETrace {

    /******* VIEWS *******/

    (:debug)
    public static function viewShown( category as String, viewName as String, depth as Number ) as Void {
        emit( "VIEW;category=" + category + ";view=" + viewName + ";depth=" + depth.toString() );
    }
    (:release)
    public static function viewShown( category as String, viewName as String, depth as Number ) as Void {}

    (:debug)
    public static function glanceViewShown() as Void {
        emit( "VIEW;category=glance;view=GlanceSitemapView;depth=1" );
    }
    (:release)
    public static function glanceViewShown() as Void {}

    (:debug)
    public static function syncViewShown( depth as Number ) as Void {
        emit( "VIEW;category=sync;view=SystemSyncView;depth=" + depth.toString() );
    }
    (:release)
    public static function syncViewShown( depth as Number ) as Void {}

    // ActionMenu is shown outside the ViewStack via WatchUi.showActionMenu().
    (:debug)
    public static function actionMenuShown( depth as Number ) as Void {
        emit( "VIEW;category=sitemap;view=ActionMenu;depth=" + depth.toString() );
    }
    (:release)
    public static function actionMenuShown( depth as Number ) as Void {}

    /******* CONNECTION *******/

    (:debug)
    public static function connectionModeChanged( mode as String ) as Void {
        emit( "CONNECTION;mode=" + mode );
    }
    (:release)
    public static function connectionModeChanged( mode as String ) as Void {}

    /******* SITEMAP REQUESTS *******/

    (:debug)
    public static function sitemapSucceeded( estimatedBytes as Number ) as Void {
        emit( "SITEMAP;result=success;code=200;bytes=" + estimatedBytes.toString() );
    }
    (:release)
    public static function sitemapSucceeded( estimatedBytes as Number ) as Void {}

    (:debug)
    public static function sitemapUnchanged( estimatedBytes as Number ) as Void {
        emit( "SITEMAP;result=unchanged;code=200;bytes=" + estimatedBytes.toString() );
    }
    (:release)
    public static function sitemapUnchanged( estimatedBytes as Number ) as Void {}

    (:debug)
    public static function sitemapFailed( code as String ) as Void {
        emit( "SITEMAP;result=error;code=" + code + ";bytes=-" );
    }
    (:release)
    public static function sitemapFailed( code as String ) as Void {}

    /******* COMMANDS *******/

    (:debug)
    public static function commandSucceeded( code as Number, item as String, value as String ) as Void {
        emit( "COMMAND;result=success;code=" + code.toString()
              + ";item=" + encode( item ) + ";value=" + encode( value ) );
    }
    (:release)
    public static function commandSucceeded( code as Number, item as String, value as String ) as Void {}

    (:debug)
    public static function commandFailed( code as String, item as String, value as String ) as Void {
        emit( "COMMAND;result=error;code=" + code
              + ";item=" + encode( item ) + ";value=" + encode( value ) );
    }
    (:release)
    public static function commandFailed( code as String, item as String, value as String ) as Void {}

    /******* INPUT *******/

    (:debug)
    public static function inputDelivered( button as String, viewName as String, depth as Number ) as Void {
        emit( "INPUT;button=" + button + ";view=" + viewName + ";depth=" + depth.toString() );
    }
    (:release)
    public static function inputDelivered( button as String, viewName as String, depth as Number ) as Void {}

    (:debug)
    public static function selectionHandled( kind as String, result as String, depth as Number ) as Void {
        // The element type comes from the server, so it is encoded like
        // COMMAND.item: a ";" or "=" in it must not break the line format.
        emit( "SELECT;kind=" + encode( kind ) + ";result=" + result + ";depth=" + depth.toString() );
    }
    (:release)
    public static function selectionHandled( kind as String, result as String, depth as Number ) as Void {}

    /******* FOCUS *******/

    /*
     * The last FOCUS line emitted, so a menu that repaints on every poll does
     * not flood the console with the entry that is already focused.
     *
     * The state is kept here rather than in the menu on purpose: a page that
     * is pushed or popped must emit again even when it returns to the entry it
     * showed before, and only a single value shared by all menus compares that
     * across views. It is compiled only in debug builds.
     */
    (:debug)
    private static var _lastFocus as String?;

    (:debug)
    public static function focusedItem(
        viewName as String,
        depth as Number,
        index as Number,
        kind as String,
        label as String
    ) as Void {
        var event = focusEvent( viewName, depth, index, kind, label );
        var last = _lastFocus;
        if( last != null && last.equals( event ) ) {
            return;
        }
        _lastFocus = event;
        emit( event );
    }
    (:release)
    public static function focusedItem(
        viewName as String,
        depth as Number,
        index as Number,
        kind as String,
        label as String
    ) as Void {}

    /*
     * The FOCUS line body, without the "E2E:" prefix.
     *
     * Kept separate from the emitter so the wire format — field order and the
     * encoding of the server-sourced kind and label — can be pinned by a unit
     * test: the E2E scenarios parse this exact shape.
     */
    (:debug)
    public static function focusEvent(
        viewName as String,
        depth as Number,
        index as Number,
        kind as String,
        label as String
    ) as String {
        return "FOCUS;view=" + viewName
               + ";depth=" + depth.toString()
               + ";index=" + index.toString()
               + ";kind=" + encode( kind )
               + ";label=" + encode( label );
    }

    /******* ERROR HANDLING *******/

    (:debug)
    public static function errorTemporary( id as String, code as String, source as String ) as Void {
        emit( "ERROR;kind=temporary;id=" + id + ";code=" + code + ";source=" + source );
    }
    (:release)
    public static function errorTemporary( id as String, code as String, source as String ) as Void {}

    (:debug)
    public static function errorCritical( id as String, code as String, source as String ) as Void {
        emit( "ERROR;kind=critical;id=" + id + ";code=" + code + ";source=" + source );
    }
    (:release)
    public static function errorCritical( id as String, code as String, source as String ) as Void {}

    (:debug)
    public static function errorFatal( id as String, code as String, source as String ) as Void {
        emit( "ERROR;kind=fatal;id=" + id + ";code=" + code + ";source=" + source );
    }
    (:release)
    public static function errorFatal( id as String, code as String, source as String ) as Void {}

    /******* TOASTS AND ERROR VIEW *******/

    (:debug)
    public static function toastShown( id as String ) as Void {
        emit( "TOAST;id=" + id );
    }
    (:release)
    public static function toastShown( id as String ) as Void {}

    (:debug)
    public static function infoToastShown( info as String or ResourceId ) as Void {
        emit( "TOAST;id=" + info.toString() );
    }
    (:release)
    public static function infoToastShown( info as String or ResourceId ) as Void {}

    (:debug)
    public static function errorViewShown( id as String, code as String ) as Void {
        emit( "ERRORVIEW;id=" + id + ";code=" + code );
    }
    (:release)
    public static function errorViewShown( id as String, code as String ) as Void {}

    /******* STARTUP *******/

    (:debug)
    public static function systemLanguage() as Void {
        emit( "LANGUAGE;code=" + languageCode( System.getDeviceSettings().systemLanguage ) );
    }
    (:release)
    public static function systemLanguage() as Void {}

    /******* HELPERS *******/

    // Percent-encodes the characters that would break the line format: the
    // field separator, the escape character itself, and the characters that
    // cannot be read on one line. Values keep their human-readable form, so a
    // scenario may assert either form.
    (:debug)
    private static function encode( value as String ) as String {
        var encoded = "";
        for( var i = 0; i < value.length(); i++ ) {
            var c = value.substring( i, i + 1 ) as String;
            if( c.equals( " " ) ) {
                encoded += "%20";
            } else if( c.equals( "|" ) ) {
                encoded += "%7C";
            } else if( c.equals( "%" ) ) {
                encoded += "%25";
            } else if( c.equals( ";" ) ) {
                encoded += "%3B";
            } else if( c.equals( "\n" ) ) {
                encoded += "%0A";
            } else if( c.equals( "\r" ) ) {
                encoded += "%0D";
            } else {
                encoded += c;
            }
        }
        return encoded;
    }

    /*
     * Maps the system language enum to a stable three-letter language code.
     * The codes are the lower-case Connect IQ language constant suffixes,
     * which follow ISO 639-2.
     */
    (:debug)
    private static function languageCode( language as System.Language ) as String {
        switch( language ) {
            case System.LANGUAGE_ARA: return "ara";
            case System.LANGUAGE_BUL: return "bul";
            case System.LANGUAGE_CES: return "ces";
            case System.LANGUAGE_CHS: return "chs";
            case System.LANGUAGE_CHT: return "cht";
            case System.LANGUAGE_DAN: return "dan";
            case System.LANGUAGE_DEU: return "deu";
            case System.LANGUAGE_DUT: return "dut";
            case System.LANGUAGE_ENG: return "eng";
            case System.LANGUAGE_EST: return "est";
            case System.LANGUAGE_FIN: return "fin";
            case System.LANGUAGE_FRE: return "fre";
            case System.LANGUAGE_GRE: return "gre";
            case System.LANGUAGE_HEB: return "heb";
            case System.LANGUAGE_HRV: return "hrv";
            case System.LANGUAGE_HUN: return "hun";
            case System.LANGUAGE_IND: return "ind";
            case System.LANGUAGE_ITA: return "ita";
            case System.LANGUAGE_JPN: return "jpn";
            case System.LANGUAGE_KOR: return "kor";
            case System.LANGUAGE_LAV: return "lav";
            case System.LANGUAGE_LIT: return "lit";
            case System.LANGUAGE_NOB: return "nob";
            case System.LANGUAGE_POL: return "pol";
            case System.LANGUAGE_POR: return "por";
            case System.LANGUAGE_RON: return "ron";
            case System.LANGUAGE_RUS: return "rus";
            case System.LANGUAGE_SLO: return "slo";
            case System.LANGUAGE_SLV: return "slv";
            case System.LANGUAGE_SPA: return "spa";
            case System.LANGUAGE_SWE: return "swe";
            case System.LANGUAGE_THA: return "tha";
            case System.LANGUAGE_TUR: return "tur";
            case System.LANGUAGE_UKR: return "ukr";
            case System.LANGUAGE_VIE: return "vie";
            case System.LANGUAGE_ZSM: return "zsm";
            default: return "unknown";
        }
    }

    // Prints a single event line. Only ever compiled into debug builds, so the
    // "E2E:" prefix and all event text are absent from release builds.
    (:debug)
    private static function emit( event as String ) as Void {
        System.println( "E2E:" + event );
    }
}
