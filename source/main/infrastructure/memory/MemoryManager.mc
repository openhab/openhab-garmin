import Toybox.Lang;
import Toybox.System;

/*
 * Helper functions for managing app memory limits.
 */
class MemoryManager {

    // Ensure at least 10 kB of free memory is available.
    private static const MIN_FREE_MEMORY as Number = 10240;

    // If less than the minimum is available, throw a catchable exception.
    // This threshold is intentionally generous but reasonable,
    // considering we only support devices with 500 kB or more of app memory.
    public static function ensureSufficientMemory() as Void {
        var freeMemory = System.getSystemStats().freeMemory;
        if( freeMemory < MIN_FREE_MEMORY ) {
            throw new OutOfMemoryException( freeMemory, MIN_FREE_MEMORY );
        }
    }
}