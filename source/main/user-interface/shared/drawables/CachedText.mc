import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * A `Text` that caches its rendered content in a shared bitmap pool.
 *
 * The interface is unchanged, so it can be used wherever a `Text` is used.
 * The drawable renders itself into a BufferedBitmap when its content changes
 * and blits that bitmap on every other draw, which avoids re-rasterizing the
 * text on each frame.
 *
 * If no explicit width is set, the width is derived from the text and the font,
 * mirroring how the SDK sizes a `Text` drawable.
 *
 * The cache can be disabled via `Config.UI_TEXT_CACHE_SIZE = 0`, in which case
 * the text is rendered on every draw as before.
 */
class CachedText extends Text {

    // The options accepted by the constructor. Mirrors the options of the
    // SDK's Text drawable, so that literals can be passed as before.
    typedef Options as { 
        :text as String or ResourceId, 
        :color as Graphics.ColorType, 
        :backgroundColor as Graphics.ColorType, 
        :font as Graphics.FontType, 
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

    // The font is required to determine the size of the rendered text when
    // no explicit width or height has been set. It is only known after the
    // SDK initialization has completed, see initialize().
    private var _font as Graphics.FontType = Graphics.FONT_MEDIUM;

    // The text is stored to be able to measure it when it changes; the SDK
    // does not provide an accessor for the text of a Text drawable.
    private var _text as String?;

    // Width of the text as rendered with the current font; 0 if unknown
    private var _measuredWidth as Number = 0;

    // Set to true if the measured width needs to be recalculated
    private var _measurementRequired as Boolean = false;

    public function initialize( options as Options ) {
        // The cache must exist before the SDK initializer runs, because the
        // SDK calls setText() from within its own initialization.
        _cache = new TextCache();
        Text.initialize( options );

        // The SDK initializer calls setText() before this code runs, so the
        // font is assigned afterwards and the width is measured lazily.
        var font = options[:font];
        _font = font != null ? font as Graphics.FontType : Graphics.FONT_MEDIUM;
        _measurementRequired = true;

        var text = options[:text];
        _text = text instanceof String ? text as String : null;
    }

    public function setText( text as String or ResourceId ) as Void {
        Text.setText( text );
        // The width of a resource string cannot be measured in advance
        _text = text instanceof String ? text as String : null;
        _measurementRequired = true;
        _cache.invalidate();
    }

    public function setColor( color as ColorType ) as Void {
        // The SDK drawable must always be updated, see TextCache.updateColor()
        Text.setColor( color );
        _cache.updateColor( color );
    }

    public function setBackgroundColor( backgroundColor as ColorType ) as Void {
        Text.setBackgroundColor( backgroundColor );
        _cache.updateBackgroundColor( backgroundColor );
    }

    // Returns the width of the text area, either explicit or measured
    private function getRenderedWidth() as Number {
        var w = width.toNumber();
        if( w > 0 ) {
            return w;
        }
        if( _measurementRequired ) {
            // If the text cannot be measured (e.g. a resource string), the
            // caller falls back to the SDK implementation.
            _measuredWidth = _text != null
                             ? TextDimensions.getTextWidthInPixels( _text as String, _font )
                             : 0;
            _measurementRequired = false;
        }
        return _measuredWidth;
    }

    // Returns the height of the text area, either explicit or derived from the font
    private function getRenderedHeight() as Number {
        var h = height.toNumber();
        return h > 0 ? h : Graphics.getFontHeight( _font );
    }

    public function draw( dc as Dc ) as Void {
        if( ! isVisible ) {
            return;
        }

        /*
         * Drawables with an explicit size are not cached.
         *
         * Rendering a Text with an explicit width and height into an offscreen
         * bitmap does not produce the same result on all devices: the label of
         * the settings menu items (see LabelMenuItem) is not drawn at all on
         * the Fenix 9 Pro, while the same code works in the simulator. Since
         * these drawables are rare compared to the menu item labels, drawing
         * them directly is both simpler and safer.
         */
        if( width.toNumber() > 0 || height.toNumber() > 0 ) {
            Text.draw( dc );
            return;
        }

        var w = getRenderedWidth();
        var h = getRenderedHeight();
        if( w <= 0 || h <= 0 ) {
            Text.draw( dc );
            return;
        }

        _entry = _cache.acquire( self, _entry, w, h );
        var entry = _entry;
        if( entry == null ) {
            // Cache disabled or not enough memory
            Text.draw( dc );
            return;
        }

        var bitmap = entry.bitmap;
        if( bitmap == null ) {
            Text.draw( dc );
            return;
        }

        if( ! _cache.isRendered( entry ) ) {
            if( ! renderInto( bitmap ) ) {
                // Rendering into the buffer failed; draw directly instead of
                // breaking the menu item
                Text.draw( dc );
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

    // Renders the text into the given bitmap. Returns false if the rendering
    // failed, in which case the caller draws directly.
    private function renderInto( bitmap as BufferedBitmapType ) as Boolean {
        var bufferDc = bitmap.getDc();
        // Erase the buffer to transparent explicitly, see CachedTextArea
        bufferDc.setColor( Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT );
        bufferDc.clear();

        // The buffer represents the area of the text, so it must be drawn at
        // the origin. The location is restored afterwards, also on errors.
        var oldX = locX;
        var oldY = locY;
        locX = 0;
        locY = 0;
        try {
            Text.draw( bufferDc );
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
