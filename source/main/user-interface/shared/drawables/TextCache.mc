import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * Helper used by `CachedText` and `CachedTextArea`.
 *
 * It tracks the content version of a text drawable and acquires the bitmap
 * entry the drawable shall render into. All caching logic lives here; the
 * drawables only forward their setters and perform the actual SDK draw call,
 * which has to happen in the subclass because it is a super call.
 *
 * The version is incremented whenever the rendered output would change, i.e.
 * when the text or the color changes. Position and size are read from the
 * drawable at draw time and do not need to be tracked: the pool reassigns a
 * bitmap of different dimensions automatically.
 *
 * Note: setters are not called by the SDK itself during initialization, so the
 * color passed in the constructor options must be set via setInitialColor().
 */
class TextCache {

    // Statistics, logged periodically in debug builds
    private static var _hits as Number = 0;
    private static var _misses as Number = 0;
    private static const STATISTICS_INTERVAL as Number = 1000;

    // Version of the content currently required
    private var _version as Number = 0;

    // Color the cached content was last rendered with; null means unknown
    private var _color as ColorType?;

    // Background color the cached content was last rendered with; null means unknown
    private var _backgroundColor as ColorType?;

    // Color the text is shown on, used to fill the buffer, see prepare();
    // null means unknown, in which case the buffer stays transparent
    private var _cacheBackgroundColor as ColorType?;

    public function initialize() {}

    /*
     * Updates the color and returns true if it changed.
     *
     * The caller must always forward the color to the SDK drawable and use the
     * return value only to decide whether the cached content is outdated. The
     * color cannot be skipped, because the SDK requires its setters to be
     * called before the drawable is used.
     */
    public function updateColor( color as ColorType ) as Boolean {
        if( _color == color ) {
            return false;
        }
        _color = color;
        _version++;
        return true;
    }

    // See updateColor()
    public function updateBackgroundColor( backgroundColor as ColorType ) as Boolean {
        if( _backgroundColor == backgroundColor ) {
            return false;
        }
        _backgroundColor = backgroundColor;
        _version++;
        return true;
    }

    /*
     * Sets the color the text is shown on, which is used to fill the buffer the
     * text is rendered into. Changing it invalidates the cached content.
     *
     * The value has to be the background of the drawable's own area, i.e. of
     * the rectangle the text is drawn into. Pass COLOR_TRANSPARENT (or nothing)
     * if the area is not of a uniform color, see prepare().
     */
    public function setCacheBackgroundColor( color as ColorType ) as Void {
        if( _cacheBackgroundColor == color ) {
            return;
        }
        _cacheBackgroundColor = color;
        _version++;
    }

    // Marks the cached content as outdated, e.g. after the text has changed
    public function invalidate() as Void {
        _version++;
    }

    /*
     * Erases a buffer and sets the colors for rendering the text into it.
     *
     * The fill color matters for the result: the antialiased glyph edges are
     * blended with it, so it must be the color the text is eventually shown
     * on. If it is not, every glyph gets a 1px halo in the fill color, which
     * is clearly visible for the grey state texts on dark menu items.
     *
     * The background of the buffer is transparent by default, because the
     * background of a menu item differs between focused and unfocused items
     * and is painted by the owning drawable. The RGB value of transparent
     * pixels still ends up in the glyph edges, so the text color is used as
     * the pen color: this way the edges are blended towards the glyph color
     * instead of towards white, which keeps the halo from standing out.
     *
     * Callers should prefer setCacheBackgroundColor() to fill the buffer with
     * the actual background, which reproduces exactly what drawing the text
     * directly would produce.
     */
    public function prepare( bufferDc as Dc ) as Void {
        var textColor = _color != null ? _color as ColorType : Graphics.COLOR_WHITE;

        // A drawable with an explicit background color paints the text on that
        // color, so it serves as the fill color as well.
        var backgroundColor = _cacheBackgroundColor != null 
                              ? _cacheBackgroundColor 
                              : _backgroundColor;

        if( backgroundColor != null && backgroundColor != Graphics.COLOR_TRANSPARENT ) {
            // Opaque fill: the glyph edges are blended with the color the text
            // is shown on, exactly as when drawing directly.
            bufferDc.setColor( textColor, backgroundColor as ColorType );
        } else {
            bufferDc.setColor( textColor, Graphics.COLOR_TRANSPARENT );
        }
        bufferDc.clear();
    }

    /*
     * Returns the entry to render into, or null if caching is disabled.
     *
     * If the caller passes the entry it obtained on a previous call, it is
     * reused when still valid. Looking up an entry in the pool requires a
     * linear scan over all entries; since a displayed drawable is drawn
     * repeatedly with the same size, that scan is avoided and the pool is only
     * consulted when the entry was recycled for another drawable.
     */
    public function acquire( 
        owner as Object, 
        entry as TextCacheEntry?, 
        width as Number, 
        height as Number 
    ) as TextCacheEntry? {
        if( entry != null
            && entry.owner == owner
            && entry.width == width
            && entry.height == height
            && entry.bitmap != null
        ) {
            // Keep the LRU order up to date, see TextBitmapPool.touch()
            TextBitmapPool.get().touch( entry );
            return entry;
        }
        return TextBitmapPool.get().acquire( owner, width, height );
    }

    // Returns true if the entry already holds the current content
    public function isRendered( entry as TextCacheEntry ) as Boolean {
        if( entry.renderedVersion == _version ) {
            _hits++;
            logStatistics();
            return true;
        }
        return false;
    }

    // Marks the entry as holding the current content
    public function markRendered( entry as TextCacheEntry ) as Void {
        entry.renderedVersion = _version;
        _misses++;
        logStatistics();
    }

    // Statistics are a debug aid; the actual output is stripped from release
    // builds by Logger.debug().
    private function logStatistics() as Void {
        var total = _hits + _misses;
        if( total % STATISTICS_INTERVAL != 0 ) {
            return;
        }
        Logger.debug( "TextCache: hits=" + _hits + " misses=" + _misses );
    }

    /*
     * Resolves the horizontal position of a blit.
     *
     * The SDK resolves layout constants internally when a drawable draws
     * itself. Since the blit is performed by the wrapper, the constants have
     * to be resolved here, see also BufferedBitmapDrawable.draw().
     */
    public static function resolveX( dcWidth as Number, width as Number, locX as Numeric ) as Number {
        if( locX == WatchUi.LAYOUT_HALIGN_CENTER ) {
            return dcWidth/2 - width/2;
        } else if( locX == WatchUi.LAYOUT_HALIGN_RIGHT ) {
            return dcWidth - width;
        } else if( locX == WatchUi.LAYOUT_HALIGN_LEFT || locX == WatchUi.LAYOUT_HALIGN_START ) {
            return 0;
        }
        return locX.toNumber();
    }

    // See resolveX()
    public static function resolveY( dcHeight as Number, height as Number, locY as Numeric ) as Number {
        if( locY == WatchUi.LAYOUT_VALIGN_CENTER ) {
            return dcHeight/2 - height/2;
        } else if( locY == WatchUi.LAYOUT_VALIGN_BOTTOM ) {
            return dcHeight - height;
        } else if( locY == WatchUi.LAYOUT_VALIGN_TOP || locY == WatchUi.LAYOUT_VALIGN_START ) {
            return 0;
        }
        return locY.toNumber();
    }
}
