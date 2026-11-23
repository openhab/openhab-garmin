import Toybox.Lang;
import Toybox.WatchUi;

/*
 * This static singleton class tracks whether toast notifications 
 * should currently be displayed and provides a function for showing them.
 */
public class WarningToastHandler {
    private static var _useToasts as Boolean = true;
    /*
    * Views should call this function in their `onShow()` method to indicate 
    * whether toast notifications should be displayed.
    *
    * Views should NOT modify this setting in `onHide()`. This allows subsequent 
    * views to inherit the state from the previous one—for example, only 
    * `HomepageMenu` needs to set it, and all following views will inherit the preference.
    */
    public static function setUseToasts( useToasts as Boolean ) as Void { 
        _useToasts = useToasts; 
    }

    /*
    * Indicates whether the current view has requested toast notifications.
    *
    * The final decision on whether a toast is shown is handled by the 
    * `ExceptionHandler`, as it considers additional factors beyond 
    * the view's preference.
    */
    public static function useToasts() as Boolean { 
        return _useToasts; 
    }

    // Show a warning toast
    public static function showWarning( warning as String ) as Void {
        // A toast is a UI overlay. While the app is hidden by the system
        // (inactive lifecycle state) it must not be shown; the next sitemap
        // update after the app becomes visible again reports the error.
        if( ! AppLifecycle.isActive() ) {
            return;
        }

        WatchUi.showToast( 
            warning.toUpper(), 
            { :icon => Rez.Drawables.iconWarning } );
    }

    /*
     * Shows a warning toast and traces it with a stable, language-independent
     * id (a resource id or code such as S:-104 or UI:03). The rendered warning
     * text is never traced.
     */
    public static function showWarningWithId( id as String, warning as String ) as Void {
        if( ! AppLifecycle.isActive() ) {
            return;
        }

        E2ETrace.toastShown( id );
        showWarning( warning );
    }

    // Show an informational toast. Unlike warnings, this is not an error
    // notification and is not gated by useToasts(), because it is a direct
    // response to a user action.
    public static function showInfo( info as String or ResourceId ) as Void {
        if( ! AppLifecycle.isActive() ) {
            return;
        }

        E2ETrace.infoToastShown( info );

        WatchUi.showToast( info, null );
    }
}
