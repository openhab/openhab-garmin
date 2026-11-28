import Toybox.Lang;
import Toybox.Test;

/*
 * Unit tests for the E2E focus trace line.
 *
 * The `E2E:FOCUS` line is a wire format shared with the harness: the scenarios
 * parse it to decide which row is highlighted, so its field order and the
 * encoding of the server-sourced kind and label are a contract, not an
 * implementation detail. These tests pin that shape on the string level, which
 * needs no view, no menu and no simulator input.
 *
 * They do NOT test when the line is emitted: that needs a drawn menu with a
 * highlight, which is the E2E tier (`focus` steps and `assert.focus`).
 *
 * `E2ETrace` is debug-only, and the unit-test build is a debug build, so the
 * methods exist here. A release build contains neither them nor these tests.
 */
(:test)
class E2ETraceFocusTests {

    // The documented order: view, depth, index, kind, label, source. A scenario
    // may rely on it, and the harness tests assert against real sample lines.
    (:test)
    static function fieldOrderIsStable( logger as Test.Logger ) as Boolean {
        var line = E2ETrace.focusEvent( "HomepageMenu", 1, 1, "Frame", "Player", "platform" );
        logger.debug( "line=" + line );

        Test.assertEqualMessage(
            "FOCUS;view=HomepageMenu;depth=1;index=1;kind=Frame;label=Player;source=platform",
            line,
            "FOCUS wire format"
        );
        return true;
    }

    // The provenance field separates a position the platform reported from the
    // baseline the app assumes when isFocused() reports nothing. A scenario may
    // assert only on the former.
    (:test)
    static function theSourceFieldSeparatesBaselineFromPlatform( logger as Test.Logger ) as Boolean {
        var platform = E2ETrace.focusEvent( "HomepageMenu", 1, 3, "Frame", "Empty", "platform" );
        var baseline = E2ETrace.focusEvent( "HomepageMenu", 1, 0, "Frame", "Switches", "baseline" );
        logger.debug( "platform=" + platform + " baseline=" + baseline );

        Test.assertMessage(
            platform.find( ";source=platform" ) != null,
            "a platform report must say so: " + platform
        );
        Test.assertMessage(
            baseline.find( ";source=baseline" ) != null,
            "a baseline must say so: " + baseline
        );
        return true;
    }

    // The label is server-sourced. A space, '%', '|' or ';' in it must not
    // break the line: ';' separates fields and '%' introduces an escape.
    (:test)
    static function labelIsPercentEncoded( logger as Test.Logger ) as Boolean {
        var line = E2ETrace.focusEvent(
            "PageMenu", 2, 0, "Switch", "Living Room; 50%|x", "platform"
        );
        logger.debug( "line=" + line );

        Test.assertEqualMessage(
            "FOCUS;view=PageMenu;depth=2;index=0;kind=Switch"
            + ";label=Living%20Room%3B%2050%25%7Cx;source=platform",
            line,
            "label encoding"
        );
        return true;
    }

    // The element type is server-sourced too, and is the same value SELECT
    // reports; a ';' in it must not be read as the next field.
    (:test)
    static function kindIsEncodedToo( logger as Test.Logger ) as Boolean {
        var line = E2ETrace.focusEvent( "PageMenu", 2, 3, "Odd;Type", "", "platform" );
        logger.debug( "line=" + line );

        Test.assertEqualMessage(
            "FOCUS;view=PageMenu;depth=2;index=3;kind=Odd%3BType;label=;source=platform",
            line,
            "kind encoding"
        );
        return true;
    }

    // An entry the app adds itself reports no label on purpose: its label is
    // translated chrome, and the trace never carries translated text. The field
    // must still be present and empty, so `label: ""` is an explicit assertion.
    (:test)
    static function anEmptyLabelIsStillAField( logger as Test.Logger ) as Boolean {
        var line = E2ETrace.focusEvent( "HomepageMenu", 1, 4, "Settings", "", "platform" );
        logger.debug( "line=" + line );

        Test.assertMessage(
            line.find( ";label=;source=" ) != null,
            "the line must carry an empty label field: " + line
        );
        return true;
    }

    // Non-ASCII labels are valid UTF-8 and carry no delimiter, so they pass
    // through unchanged; the harness compares the decoded value.
    (:test)
    static function aNonAsciiLabelPassesThrough( logger as Test.Logger ) as Boolean {
        var line = E2ETrace.focusEvent( "PageMenu", 2, 1, "Text", "Téléphone", "platform" );
        logger.debug( "line=" + line );

        Test.assertEqualMessage(
            "FOCUS;view=PageMenu;depth=2;index=1;kind=Text;label=Téléphone;source=platform",
            line,
            "non-ASCII label"
        );
        return true;
    }
}
