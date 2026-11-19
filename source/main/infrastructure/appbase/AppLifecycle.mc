import Toybox.Lang;

/*
 * Tracks the active/inactive app lifecycle introduced with API level 4.2.3.
 *
 * On devices that support it (e.g. fēnix 8/9, Venu 3/4, vívoactive 5/6) the
 * system hides the app instead of terminating it when the user leaves it, for
 * example by returning to the watch face. The app keeps running in the
 * inactive state, but access to system resources is limited.
 *
 * On older devices AppBase.onActive()/onInactive() are never called, so the
 * app stays active because it is terminated when it is left.
 *
 * AppBase.isActive() cannot be used here: it does not exist on older devices
 * and referencing it would break compilation for them.
 */
class AppLifecycle {

    // The app is visible when it starts and remains visible on devices
    // without the lifecycle.
    private static var _isActive as Boolean = true;

    // True if the app currently occupies the screen.
    // Used to suppress UI operations triggered from background callbacks
    // while the app is hidden.
    public static function isActive() as Boolean {
        return _isActive;
    }

    // Called from OHApp.onActive()
    public static function setActive() as Void {
        _isActive = true;
    }

    // Called from OHApp.onInactive()
    public static function setInactive() as Void {
        _isActive = false;
    }
}