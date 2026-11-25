import Toybox.Lang;
import Toybox.Test;
import Toybox.Communications;

/*
 * Unit tests for the error classification tier.
 *
 * `ExceptionHandler` decides how an error is presented, and that decision is
 * made from two things: the predicates of the exception itself
 * (`requiresFullScreenError()`, `isNoPhone()`, `suppressAsEmptyResponse()`) and
 * the source of the error (sitemap or command). These tests pin the predicates
 * and the user-facing codes, which is the part that has no dependency on a view.
 *
 * They deliberately do NOT assert what the user sees: `ExceptionHandler` needs
 * a view stack, so presentation stays in the E2E tier.
 */
(:test)
class ErrorClassificationTests {

    // The documented hard limit of the pinned simulator: 16384 bytes are
    // accepted, 16385 are rejected with -402. The code it produces must be a
    // full-screen error and a stable user-facing code.
    (:test)
    static function sizeLimitIsCritical( logger as Test.Logger ) as Boolean {
        var ex = new CommunicationException(
            Communications.NETWORK_RESPONSE_TOO_LARGE,
            CommunicationBaseException.EX_SOURCE_SITEMAP
        );
        logger.debug( "toast=" + ex.getToastMessage() + " code=" + ex.getResponseCode() );

        Test.assertEqualMessage( "S", ex.getSourceShortCode(), "sitemap source prefix" );
        Test.assertEqualMessage( "S:-402", ex.getToastMessage(), "size limit toast code" );
        Test.assertMessage( ex.requiresFullScreenError(), "-402 must be a full-screen error" );
        Test.assertMessage( ! ex.isNoPhone(), "-402 is not a connection error" );
        Test.assertMessage( ! ex.suppressAsEmptyResponse(), "-402 is not an empty response" );

        var message = ex.getErrorMessage() as String;
        Test.assertMessage( message.find( "-402" ) != null,
            "the error message must name the response code" );
        return true;
    }

    (:test)
    static function otherCriticalCodesRequireFullScreen( logger as Test.Logger ) as Boolean {
        var codes = [
            Communications.NETWORK_RESPONSE_OUT_OF_MEMORY,
            Communications.SECURE_CONNECTION_REQUIRED,
            404
        ];
        for( var i = 0; i < codes.size(); i++ ) {
            var ex = new CommunicationException(
                codes[i],
                CommunicationBaseException.EX_SOURCE_SITEMAP
            );
            logger.debug( "code=" + codes[i] + " fullScreen=" + ex.requiresFullScreenError() );
            Test.assertMessage( ex.requiresFullScreenError(),
                "code " + codes[i] + " must be a full-screen error" );
        }
        return true;
    }

    (:test)
    static function noPhoneErrorsAreDetectedAndTemporary( logger as Test.Logger ) as Boolean {
        var codes = [
            Communications.BLE_CONNECTION_UNAVAILABLE,
            Communications.BLE_HOST_TIMEOUT
        ];
        for( var i = 0; i < codes.size(); i++ ) {
            var ex = new CommunicationException(
                codes[i],
                CommunicationBaseException.EX_SOURCE_SITEMAP
            );
            logger.debug( "code=" + codes[i] + " noPhone=" + ex.isNoPhone() );
            Test.assertMessage( ex.isNoPhone(), "code " + codes[i] + " must report no phone" );
            Test.assertMessage( ! ex.requiresFullScreenError(),
                "code " + codes[i] + " must not start as a full-screen error" );
        }
        return true;
    }

    /*
     * The source, not the code, decides presentation for command errors:
     * ExceptionHandler shows every command error as a toast. The predicates
     * below are code-based, so they look identical for both sources; the test
     * pins the source marker that the dispatcher keys on.
     */
    (:test)
    static function sourcePrefixSeparatesSitemapFromCommand( logger as Test.Logger ) as Boolean {
        var command = new CommunicationException(
            Communications.NETWORK_RESPONSE_TOO_LARGE,
            CommunicationBaseException.EX_SOURCE_COMMAND
        );
        logger.debug( "command toast=" + command.getToastMessage() );

        Test.assertEqualMessage( "C", command.getSourceShortCode(), "command source prefix" );
        Test.assertEqualMessage( "C:-402", command.getToastMessage(), "command toast code" );
        Test.assertMessage( command.isFrom( CommunicationBaseException.EX_SOURCE_COMMAND ),
            "command exception must report the command source" );
        Test.assertMessage( ! command.isFrom( CommunicationBaseException.EX_SOURCE_SITEMAP ),
            "command exception must not report the sitemap source" );
        return true;
    }

    /*
     * "Suppress empty response errors" must cover both shapes an empty
     * myopenHAB answer can take: -400 and HTTP 200 with a null body. A null
     * body suppresses regardless of the source; -400 does not, because only a
     * sitemap response is known to be empty that way.
     */
    (:test)
    static function emptyResponseSuppression( logger as Test.Logger ) as Boolean {
        var sitemap400 = new CommunicationException(
            Communications.INVALID_HTTP_BODY_IN_NETWORK_RESPONSE,
            CommunicationBaseException.EX_SOURCE_SITEMAP
        );
        var command400 = new CommunicationException(
            Communications.INVALID_HTTP_BODY_IN_NETWORK_RESPONSE,
            CommunicationBaseException.EX_SOURCE_COMMAND
        );
        var sitemapNull = new UnexpectedResponseException(
            null,
            CommunicationBaseException.EX_SOURCE_SITEMAP
        );
        var sitemapData = new UnexpectedResponseException(
            "unexpected",
            CommunicationBaseException.EX_SOURCE_SITEMAP
        );

        logger.debug( "sitemap400=" + sitemap400.suppressAsEmptyResponse()
            + " command400=" + command400.suppressAsEmptyResponse()
            + " sitemapNull=" + sitemapNull.suppressAsEmptyResponse() );

        Test.assertMessage( sitemap400.suppressAsEmptyResponse(), "-400 from a sitemap is suppressible" );
        Test.assertMessage( ! command400.suppressAsEmptyResponse(), "-400 from a command is not" );
        Test.assertMessage( sitemapNull.suppressAsEmptyResponse(), "a null sitemap body is suppressible" );
        Test.assertMessage( ! sitemapData.suppressAsEmptyResponse(), "unexpected data is not suppressed" );
        return true;
    }
}
