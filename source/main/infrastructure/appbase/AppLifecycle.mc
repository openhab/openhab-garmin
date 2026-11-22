import Toybox.Lang;

/*
 * Tracks whether the app process runs as a glance and, for the widget,
 * whether it is currently visible.
 *
 * The app is always in exactly one of these states:
 *
 * - ACTIVE:   the widget process is shown in the foreground.
 * - INACTIVE: the widget process was hidden by the system, e.g. when the
 *             watch face is shown. Only used on API level 4.2.3+; on older
 *             devices onActive()/onInactive() are never called and the app is
 *             terminated when it is left, so it stays ACTIVE.
 * - GLANCE:   the glance process. It has no foreground widget UI and is not
 *             affected by the active/inactive lifecycle.
 *
 * ACTIVE and INACTIVE are dynamic, GLANCE is terminal for the process
 * lifetime. GLANCE is only known once getGlanceView() has run, so callbacks
 * that may run before it (e.g. onSettingsChanged() in a process created just
 * to deliver the callback) must not rely on isGlance().
 *
 * AppBase.isActive() cannot be used here: it does not exist on older devices
 * and referencing it would break compilation for them.
 */
(:glance)
class AppLifecycle {

    public enum State {
        ACTIVE,     // Widget process, visible
        INACTIVE,   // Widget process, hidden by the system
        GLANCE      // Glance process
    }

    // The app is visible when it starts and remains visible on devices
    // without the active/inactive lifecycle. Before getGlanceView() runs, a
    // glance process therefore also looks like an active widget process.
    private static var _state as State = ACTIVE;

    // Returns the current state, e.g. for logging.
    public static function getState() as State {
        return _state;
    }

    // True if the widget is shown in the foreground. Used to suppress UI
    // operations triggered from background callbacks while the app is hidden.
    // False in the glance process, which has no foreground widget UI.
    public static function isActive() as Boolean {
        return _state == ACTIVE;
    }

    // True if the widget was hidden by the system and keeps running.
    public static function isInactive() as Boolean {
        return _state == INACTIVE;
    }

    // True if this process runs as a glance. See the ordering note above.
    public static function isGlance() as Boolean {
        return _state == GLANCE;
    }

    // Called from OHApp.getGlanceView().
    public static function setGlance() as Void {
        _state = GLANCE;
    }

    // Called from OHApp.onActive()
    public static function setActive() as Void {
        if( _state != GLANCE ) {
            _state = ACTIVE;
        }
    }

    // Called from OHApp.onInactive()
    public static function setInactive() as Void {
        if( _state != GLANCE ) {
            _state = INACTIVE;
        }
    }
}
