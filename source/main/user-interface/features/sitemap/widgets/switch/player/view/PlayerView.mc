import Toybox.Lang;
import Toybox.WatchUi;
import Toybox.Graphics;

/*
 * Full-screen view based on ControlView to display a player switch.
 * Provides "Play"/"Pause", "Next", and "Previous" functions.
 *
 * This class handles display only. User input is managed by `PlayerViewDelegate`,
 * and commands are sent via `PlayerMenuItem`.
 */
class PlayerView extends ControlView {

    // The sitemap element associated with this view
    private var _sitemapSwitch as SitemapSwitch;

    // For drawing the title we use a TextArea, which
    // dynamically chooses the font size and applies line breaks if needed
    private var _titleDrawable as TextArea?;
    
    // The centered bitmap for the play/pause adapts based on the state
    private var _playPauseIcon as PlayPauseBitmap?;

    // The previous/next buttons. They are drawn as separate bitmaps on
    // touch-based devices only. They are kept, because their icons depend
    // on the theme, which can change while the view is open.
    (:exclForButton)
    private var _previousIcon as InteractiveRelativeBitmap?;
    (:exclForButton)
    private var _nextIcon as InteractiveRelativeBitmap?;

    // Also the input hint adapts based on the state
    (:exclForTouch)
    private var _playPauseHint as InputHint?;

    // Constructor
    public function initialize( sitemapSwitch as SitemapSwitch ) {
        ControlView.initialize();
        _sitemapSwitch = sitemapSwitch;
    }

    // Returns the input hint type to be shown for a specific state
    (:exclForTouch)
    private function getInputHintType ( currentState as String ) as BaseInputHint.Type {
        return currentState.equals( SwitchItem.ITEM_STATE_PLAY )
            ? BaseInputHint.HINT_TYPE_PAUSE
            : BaseInputHint.HINT_TYPE_PLAY;
    }

    // onLayout is called once when the view is opened,
    // and initiates all the Drawables   
    // Implementation for button-based devices puts the title in the center
    // and input hints around it
    (:exclForTouch)
    public function onLayout( dc as Dc ) as Void {
        // Logger.debug( "RollershutterView.onLayout" ) );

        var dcHeight = dc.getHeight();
        var dcWidth = dc.getWidth();

        // Add the title and connection mode indicator using 
        // the standard layout provided by the base class
        _titleDrawable = addTitleAndConnectionIndicator(
            _sitemapSwitch.getLabel(),
            dcWidth,
            dcHeight
        );

        // The play/pause button
        _playPauseIcon = new PlayPauseBitmap( {
            :state => _sitemapSwitch.getSwitchItem().getState(),
            :locX => WatchUi.LAYOUT_HALIGN_CENTER,
            :locY => 0.55
        } );
        addDrawable( _playPauseIcon );

        addTouchArea( 
            new CircularTouchArea( 
                :touchPlayPause, 
                ( dcWidth/2 ).toNumber(), 
                ( dcHeight/2 ).toNumber(), 
                ( dcWidth*0.30 ).toNumber() 
        ) );

        _playPauseHint = addInputHint( 
            InputHint.HINT_KEY_ENTER, 
            getInputHintType( _sitemapSwitch.getSwitchItem().getState() ), 
            :touchPlayPause 
        );
        addInputHint( InputHint.HINT_KEY_UP, InputHint.HINT_TYPE_PREVIOUS, :touchPrevious );
        addInputHint( InputHint.HINT_KEY_DOWN, InputHint.HINT_TYPE_NEXT, :touchNext );
    }

    // Implementation for touch-based devices puts the title at the top and
    // icons for play/pause, next, previous underneath
    (:exclForButton)
    public function onLayout( dc as Dc ) as Void {
        // Logger.debug( "RollershutterView.onLayout" ) );

        var dcHeight = dc.getHeight();
        var dcWidth = dc.getWidth();

        // Add the title and connection mode indicator using 
        // the standard layout provided by the base class
        _titleDrawable = addTitleAndConnectionIndicator(
            _sitemapSwitch.getLabel(),
            dcWidth,
            dcHeight
        );

        // The play/pause button
        _playPauseIcon = new PlayPauseBitmap( {
            :state => _sitemapSwitch.getSwitchItem().getState(),
            :locX => WatchUi.LAYOUT_HALIGN_CENTER,
            :locY => 0.8,
            :touchId => :touchPlayPause
        } );
        addDrawable( _playPauseIcon );

        // The next/previous buttons
        _previousIcon = new InteractiveRelativeBitmap( {
            :rezId => ThemeManager.current.iconPrevious,
            :locX => 0.2,
            :locY => WatchUi.LAYOUT_VALIGN_CENTER,
            :touchId => :touchPrevious
        } );
        addDrawable( _previousIcon );
        _nextIcon = new InteractiveRelativeBitmap( {
            :rezId => ThemeManager.current.iconNext,
            :locX => 0.8,
            :locY => WatchUi.LAYOUT_VALIGN_CENTER,
            :touchId => :touchNext
        } );
        addDrawable( _nextIcon );
    }

    // Applies the current theme to the theme-dependent icons.
    // onLayout() is only called once, so the icons have to be refreshed
    // before drawing when the theme changes while the view is open.
    (:exclForButton)
    private function updateTheme() as Void {
        if( _previousIcon != null ) {
            _previousIcon.setResourceId( ThemeManager.current.iconPrevious );
        }
        if( _nextIcon != null ) {
            _nextIcon.setResourceId( ThemeManager.current.iconNext );
        }
    }
    (:exclForTouch)
    private function updateTheme() as Void {}

    public function onUpdate( dc as Dc ) as Void {
        updateTheme();

        if( _playPauseIcon != null ) {
            _playPauseIcon.updateTheme();
        }

        ControlView.onUpdate( dc );
    }

    // Updates the input hint
    // Implementation is needed for button-based devices only
    (:exclForTouch)
    private function updateInputHint( sitemapSwitch as SitemapSwitch ) as Void {
        // Update the play/pause hint
        if( _playPauseHint != null ) {
            _playPauseHint.setType( 
                getInputHintType( _sitemapSwitch.getSwitchItem().getState() ) 
            );
        }
    }
    (:exclForButton)
    private function updateInputHint( sitemapSwitch as SitemapSwitch ) as Void {}

    // This function is called when an updated sitemap is received.
    // In this case it is not necessary to call WatchUi.requestUpdate(),
    // since this is done by the update algorithm
    public function updateWidget( sitemapSwitch as SitemapSwitch ) as Void {
        _sitemapSwitch = sitemapSwitch;
        
        // Update the title in case it changed
        if( _titleDrawable != null ) {
            _titleDrawable.setText( _sitemapSwitch.getLabel() );
        }
        
        // Update the play/pause button
        if( _playPauseIcon != null ) {
            _playPauseIcon.setState( _sitemapSwitch.getSwitchItem().getState() );
        }

        // Update the play/pause hint
        updateInputHint( _sitemapSwitch );
    }
}