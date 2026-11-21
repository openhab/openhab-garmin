# Folder `test`

Contains unit tests based on the Connect IQ test framework.

The tests are compiled only when unit tests are enabled and are not part of release builds.
See [Running Unit Tests](../../README.md#running-unit-tests) in the root README for how to run them.

---

## Key Concepts

Test functions are global functions annotated with `(:test)` and returning a `Boolean`.
Assertions are provided by `Toybox.Test`.

Keep tests focused on behavior that is introduced or fixed, and cover the relevant edge cases.
The test source is part of the build's source path, but the compiler excludes the `:test` annotated functions from builds that do not enable unit tests.

---

## Folder Structure

Tests are grouped by the production classes they cover, for example `SitemapTextTest.mc` covers the sitemap data layer's `SitemapText` element.

The sitemap data layer can be exercised without a running server: construct the element from a `JsonAdapter` that wraps a dictionary matching the openHAB JSON structure.
Use a `SyncTaskQueue` to build nested pages synchronously when a test needs access to the contained widgets.
