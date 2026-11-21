import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * A `TextArea` that caches its rendered content in a shared bitmap pool.
 *
 * The interface is unchanged, so it can be used wherever a `TextArea` is used.
 * The drawable renders itself into a BufferedBitmap when its content changes
 * and blits that bitmap on every other draw, which avoids re-rasterizing the
 * text on each frame. This matters especially while scrolling on touch
 * devices, where all visible menu items are redrawn on every animation frame.
 *
 * The cache can be disabled via `Config.UI_TEXT_CACHE_SIZE = 0`, in which case
 * the text is rendered on every draw as before.
 */
class CachedTextArea extends TextArea {

    // The options accepted by the constructor. Mirrors the options of the
    // SDK's TextArea drawable, so that literals can be passed as before.
    typedef Options as { 
        :text as String or ResourceId, 
        :color as Graphics.ColorType, 
        :backgroundColor as Graphics.ColorType, 
        :font as Array<Graphics.FontType> or Graphics.FontDefinition, 
        :justification as Graphics.TextJustification or Number, 
        :identifier as Object, 
        :locX as Numeric, 
        :locY as Numeric, 
        :width as Numeric, 
        :height as Numeric, 
        :visible as Boolean 
    };

    private var _cache as TextCache;

    // The entry currently used by this drawable. Kept to avoid the pool lookup
    // on every draw; it is validated before use, see TextCache.acquire().
    private var _entry as TextCacheEntry?;

    public function initialize( options as Options ) {
        // The cache must exist before the SDK initializer runs, because the
        // SDK calls setText() from within its own initialization.
        _cache = new TextCache();
        TextArea.initialize( options );
    }

    public function setText( text as String or ResourceId ) as Void {
        TextArea.setText( text );
        _cache.invalidate();
    }

    public function setColor( color as ColorType ) as Void {
        // The SDK drawable must always be updated, see TextCache.updateColor()
        TextArea.setColor( color );
        _cache.updateColor( color );
    }

    public function setBackgroundColor( backgroundColor as ColorType ) as Void {
        TextArea.setBackgroundColor( backgroundColor );
        _cache.updateBackgroundColor( backgroundColor );
    }

    /*
     * Sets the color the text is shown on, which is used to fill the buffer the
     * text is rendered into. See TextCache.prepare().
     *
     * Defaults to transparent, in which case the buffer keeps its transparent
     * background. Callers should set the background color of the drawable's
     * area whenever it is a uniform color, as only then the antialiased glyph
     * edges reproduce what drawing the text directly would produce.
     */
    public function setCacheBackgroundColor( color as ColorType ) as Void {
        _cache.setCacheBackgroundColor( color );
    }

    public function draw( dc as Dc ) as Void {
        if( ! isVisible ) {
            return;
        }

        var w = width.toNumber();
        var h = height.toNumber();
        if( w <= 0 || h <= 0 ) {
            TextArea.draw( dc );
            return;
        }

        _entry = _cache.acquire( self, _entry, w, h );
        var entry = _entry;
        if( entry == null ) {
            // Cache disabled or not enough memory
            TextArea.draw( dc );
            return;
        }

        var bitmap = entry.bitmap;
        if( bitmap == null ) {
            TextArea.draw( dc );
            return;
        }

        if( ! _cache.isRendered( entry ) ) {
            if( ! renderInto( bitmap ) ) {
                // Rendering into the buffer failed; draw directly instead of
                // breaking the menu item
                TextArea.draw( dc );
                return;
            }
            _cache.markRendered( entry );
        }

        dc.drawBitmap( 
            TextCache.resolveX( dc.getWidth(), w, locX ),
            TextCache.resolveY( dc.getHeight(), h, locY ),
            bitmap 
        );
    }

    // Renders the text area into the given bitmap. Returns false if the
    // rendering failed, in which case the caller draws directly.
    private function renderInto( bitmap as BufferedBitmapType ) as Boolean {
        var bufferDc = bitmap.getDc();
        // Erase the buffer with the colors the text has to be rendered on,
        // see TextCache.prepare() for why the fill color matters.
        _cache.prepare( bufferDc );

        // The buffer represents the area of the text area, so it must be drawn
        // at the origin. The location is restored afterwards, also on errors.
        var oldX = locX;
        var oldY = locY;
        locX = 0;
        locY = 0;
        try {
            TextArea.draw( bufferDc );
        } catch( ex ) {
            locX = oldX;
            locY = oldY;
            Logger.debugException( ex );
            return false;
        }
        locX = oldX;
        locY = oldY;
        return true;
    }
}
