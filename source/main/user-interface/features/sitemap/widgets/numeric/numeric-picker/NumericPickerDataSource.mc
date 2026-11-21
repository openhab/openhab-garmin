import Toybox.Lang;
import Toybox.WatchUi;

/*
 * Data source that provides the elements displayed by the DynamicPicker.
 * It uses the Setpoint/Slider configuration from SitemapNumeric to generate
 * a list of options based on minValue, maxValue, and step.
 *
 * If the current state does not match any of the defined steps,
 * an additional Drawable is inserted at the correct position
 * between the two nearest steps to represent the current value. 
 * Once the user scrolls away from the additional Drawable, it will be removed.
 */
class NumericPickerDataSource extends DynamicPickerDataSource {
    // The list of Drawables that are shown by the Picker
    private var _pickables as Array<DynamicPickOption> = [];
    
    // The currently selected index
    private var _currentIndex as Number = -1;
    // Stores an additional Drawable if needed to represent
    // a current state that doesn't conform to the widgets's parameters.
    private var _nonConforming as NumericPickable?;

    // Constructor
    // Builds the list of drawables
    public function initialize( menuItem as NumericMenuItem ) {
        DynamicPickerDataSource.initialize();

        // Get the data we need for building the list
        var sitemapNumeric = menuItem.getSitemapNumeric();
        var numericItem = sitemapNumeric.getNumericItem();

        // Start at minValue and increment by step
        // until the next value would exceed maxValue.
        // If maxValue is not an exact multiple of the step,
        // the final entry will be the last step below maxValue.
        var minValue = sitemapNumeric.getMinValue();
        var maxValue = sitemapNumeric.getMaxValue();
        var step = sitemapNumeric.getStep();

        // If there is no valid state, we set the current
        // value to the step closest to the middle of the range
        var currentValue = 
            numericItem.hasState()
            ? numericItem.getNumericState()
            : ( minValue + Math.round( ( maxValue-minValue ) / ( 2*step ) ) * step ).toFloat();

        for( var i = minValue; i <= maxValue; i += step ) {
            // If the index for the current value has not been set yet,
            // and we have surpassed it, we set the current index ...
            if( _currentIndex == -1 && currentValue <= i ) {
                _currentIndex = _pickables.size();
                // ... and if the current value does not align with the indexed
                // steps, we create a "non-conforming" element. This also covers
                // the case where the current value is lower than the minValue
                if( i != currentValue ) {
                    _nonConforming = new NumericPickable( currentValue, sitemapNumeric );
                    _pickables.add( _nonConforming );
                }
            }
            _pickables.add( new NumericPickable( i, sitemapNumeric ) );
        }

        // If at the end of the loop _currentIndex is still not set
        // the current value is above the maxValue. In this case
        // we add a "non-conforming" element on top
        if( _currentIndex == -1 ) {
            _currentIndex = _pickables.size();
            _nonConforming = new NumericPickable( currentValue, sitemapNumeric );
            _pickables.add( _nonConforming );
        }
    }

    // Implementation of the functions used by DynamicPicker
    public function getCurrent() as DynamicPickOption {
        return _pickables[_currentIndex];
    }

    // If up() was selected and we were on the non-conforming
    // Pickable, we do not need to increase the index
    // because removing the non-conforming Pickable will
    // automatically move the higher value to the current position.
    public function up() as Void {
        if( ! consumeNonconforming() ) {
            _currentIndex++;            
        }
        if( _currentIndex >= _pickables.size() ) {
            _currentIndex = 0;
        }
    }

    // When moving down, we decrease even if we were on the
    // non-conforming value
    public function down() as Void {
        consumeNonconforming();
        _currentIndex--;
        if( _currentIndex < 0 ) {
            _currentIndex = _pickables.size() - 1;
        }
    }

    // Checks if we are still on the non-conforming
    // Pickable, and if yes removes it and return true,
    // otherwise false
    private function consumeNonconforming() as Boolean {
        if( _nonConforming != null ) {
            _pickables.remove( _nonConforming );
            _nonConforming = null;
            return true;
        } else {
            return false;
        }
    }
}