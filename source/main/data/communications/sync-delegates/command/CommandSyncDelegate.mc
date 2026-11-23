import Toybox.Lang;
import Toybox.Application;
import Toybox.Communications;
import Toybox.WatchUi;

/*
 * Sync delegate for sending commands over Wi-Fi.
 *
 * This delegate also implements the CommandRequestDelegate interface and can
 * therefore be passed to a command request as a dummy menu item. The command
 * request is then executed in sync mode. See BaseCommandRequest for details.
 */
class CommandSyncDelegate extends BaseSyncDelegate {

    /******* STATIC *******/ 

    // Singleton instance and accessor
    private static var _instance as CommandSyncDelegate?;

    public static function get() as CommandSyncDelegate {
        if( _instance == null ) {
            _instance = new CommandSyncDelegate();
        }
        return _instance as CommandSyncDelegate;
    }

    /******* INSTANCE *******/ 

    // The command consists of item (index 0) and command (index 1)
    typedef WifiSyncCommand as [String,String];

    // Set if a command shall be executed, cleared after the execution
    // is done
    private var _cmd as WifiSyncCommand?;
    
    // Constructor
    private function initialize() {
        // Logger.debug( "CommandSyncDelegate.initialize" );
        BaseSyncDelegate.initialize();
    }

    // Overrides a method from the parent class to clean up the command.
    // Must not be called from outside. The method is public only due to
    // a Monkey C limitation that prevents overriding protected or
    // private methods.
    public function onSyncFinished() as Void {
        // Logger.debug( "CommandSyncDelegate.onSyncFinished" );
        _cmd = null;
        BaseSyncDelegate.onSyncFinished();
    }

    // Helper function that can be used to access the command,
    // while ensuring that a command is present.
    private function getCommand() as WifiSyncCommand {
        if( _cmd != null ) {
            return _cmd;
        } else {
            // Logger.debug( "CommandSyncDelegate.getCommand: command is missing" );
            throw new GeneralException( "CommandSyncDelegate: command is missing" );
        }
    }

    // Provides the item name to the command request
    // Part of the CommandRequestDelegate interface
    public function getItemName() as String {
        // Logger.debug( "CommandSyncDelegate.getItemName" );
        return getCommand()[0];
    }

    // Is called when the command request is successful and terminates
    // the sync mode
    // Part of the CommandRequestDelegate interface
    public function onCommandComplete() as Void {
        finishSync();
    }

    // Part of the CommandRequestDelegate interface
    // This method is required by the interface, but should never be called,
    // since we are already in sync mode
    public function onCommandDeferredToSync() as Void {
        onSyncException( new GeneralException( "CommandSyncDelegate.onCommandDeferredToSync" ) );
    }

    // Is called by the command request if there is an exception
    // Part of the CommandRequestDelegate interface
    public function onCommandException( ex as Exception ) as Void {
        onSyncException( ex );
    }

    // Called by the base class to perform the actual sync tasks.
    // Uses the standard command request in dedicated sync mode
    // to send the command.
    // Must not be called from outside. The method is public only due to
    // a Monkey C limitation that prevents overriding protected or
    // private methods.
    public function performSync() as Void {
        // Logger.debug( "CommandSyncDelegate.performSync" );
        
        // Create the command request with self as dummy-item and 
        // true indicating that it should be in sync mode
        var commandRequest = BaseCommandRequest.get( self, true );
            
        // Send the command
        if( commandRequest != null ) {
            commandRequest.sendCommand( getCommand()[1] );
        } else {
            throw new GeneralException( Application.loadResource( Rez.Strings.ErrorNoCommandTransport ) as String );
        }
    }

    // Triggers the sync mode and sends the given command
    public function sendCommand( cmd as WifiSyncCommand ) as Void {
        // Logger.debug( "CommandSyncDelegate.sendCommand" );
        _cmd = cmd;
        startSync( Application.loadResource( Rez.Strings.SyncSendingCommand ) as String );
    }
}