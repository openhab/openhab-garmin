import Toybox.Lang;
import Toybox.Graphics;
import Toybox.WatchUi;

/*
 * A single entry in the text bitmap pool.
 *
 * An entry owns one BufferedBitmap of a fixed size. Entries are reused by
 * different drawables: when the pool runs out of free entries, the least
 * recently used one is handed to the requesting drawable. `renderedVersion`
 * records which version of the owner's content the bitmap currently holds, so
 * that a drawable can detect that it must render again.
 */
class TextCacheEntry {
    // The drawable currently using the entry, or null if unused
    public var owner as Object?;
    public var width as Number = 0;
    public var height as Number = 0;
    public var bitmap as BufferedBitmapType?;
    // Content version the bitmap content corresponds to; -1 means the entry
    // has to be rendered before it can be used
    public var renderedVersion as Number = -1;
    // Timestamp used for LRU replacement
    public var lastUsed as Number = 0;
}

/*
 * Shared, size limited pool of BufferedBitmaps used to cache rendered text.
 *
 * Text cannot be cached per drawable, because menu items exist for every
 * sitemap element on every page, while only a few of them are displayed at
 * the same time. The pool therefore holds a small number of bitmaps
 * (`Config.UI_TEXT_CACHE_SIZE`) and hands them to whichever drawable is
 * currently being drawn.
 *
 * During scrolling the displayed items stay the same, so after the first frame
 * almost every draw is a cache hit. When an item leaves the screen, its entry
 * is recycled for the item entering it, which means exactly one text has to be
 * rendered per scroll step.
 *
 * The pool never destroys bitmaps of matching size; it reassigns them instead.
 * This avoids allocating from the graphics pool while scrolling.
 *
 * A cache size of 0 disables the pool, in which case `acquire()` returns null
 * and the drawables render their text directly.
 */
class TextBitmapPool {

    private static var _instance as TextBitmapPool?;

    public static function get() as TextBitmapPool {
        if( _instance == null ) {
            _instance = new TextBitmapPool();
        }
        return _instance as TextBitmapPool;
    }

    // Constructor is only used to make it private
    private function initialize() {}

    private var _entries as Array<TextCacheEntry> = [];

    // Monotonically increasing clock used for LRU replacement
    private var _clock as Number = 0;

    // Number of entries, 0 disables the cache
    private function getCapacity() as Number {
        return Config.UI_TEXT_CACHE_SIZE;
    }

    /*
     * Returns the entry that shall be used to draw an owner's text of the given
     * size, or null if the cache is disabled or no bitmap could be allocated.
     *
     * The caller must render into the returned entry if `renderedVersion` does
     * not match its own content version.
     */
    public function acquire( 
        owner as Object, 
        width as Number, 
        height as Number 
    ) as TextCacheEntry? {

        var capacity = getCapacity();
        if( capacity <= 0 || width <= 0 || height <= 0 ) {
            return null;
        }

        _clock++;

        var candidate = null as TextCacheEntry?;
        var oldest = null as TextCacheEntry?;

        for( var i = 0; i < _entries.size(); i++ ) {
            var entry = _entries[i];
            if( entry.owner == owner ) {
                if( entry.width == width && entry.height == height ) {
                    // The owner already has a suitable entry
                    entry.lastUsed = _clock;
                    return entry;
                }
                // The owner changed its size, so the entry can be reused
                candidate = entry;
                break;
            }
            if( oldest == null || entry.lastUsed < ( oldest as TextCacheEntry ).lastUsed ) {
                oldest = entry;
            }
        }

        if( candidate == null ) {
            // Prefer a free slot over taking over an entry that is currently
            // used by another drawable: menu items are drawn repeatedly, and
            // stealing their entries would cause additional renders.
            if( _entries.size() < capacity ) {
                var newEntry = new TextCacheEntry();
                _entries.add( newEntry );
                candidate = newEntry;
            }
        }

        if( candidate == null ) {
            // Reuse an entry with matching dimensions, so that no new bitmap
            // needs to be allocated. The content will be rendered again anyway.
            for( var i = 0; i < _entries.size(); i++ ) {
                var entry = _entries[i];
                if( entry.width == width && entry.height == height ) {
                    candidate = entry;
                    break;
                }
            }
        }

        if( candidate == null ) {
            candidate = oldest;
        }

        if( candidate == null ) {
            return null;
        }

        var result = candidate as TextCacheEntry;

        if( result.bitmap == null || result.width != width || result.height != height ) {
            try {
                result.bitmap = BufferedBitmapFactory.createBufferedBitmap( { 
                    :width => width, 
                    :height => height 
                } );
            } catch( ex ) {
                // Not enough memory: drop the entry and let the caller render
                // the text directly instead of failing the draw
                _entries.remove( result );
                return null;
            }
            result.width = width;
            result.height = height;
        }

        result.owner = owner;
        result.renderedVersion = -1;
        result.lastUsed = _clock;
        return result;
    }
}
