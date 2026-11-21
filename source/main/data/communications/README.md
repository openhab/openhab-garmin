# Folder `communications`

Responsible for all communication with the openHAB server.

This folder contains the connection management logic, HTTP request handling and persistence mechanisms required to keep the application state in sync with openHAB.

---

## Key Concepts

### Connection Mode

The app supports two connection modes: Bluetooth Low Energy (BLE) via the paired phone and direct Wi-Fi.

The BLE connection is effectively always on and allows regular polling of the sitemap, including state updates. In contrast, using Wi-Fi requires entering a dedicated Garmin sync mode. During this process, native Garmin system views are shown to indicate sync progress and completion.

Because of this limitation, no item states are displayed while operating in Wi-Fi mode. Commands can still be sent, but they are executed within the sync workflow. A manual sitemap refresh can also be triggered from the settings menu, though this updates only the sitemap structure and does not display live states.

The decision not to display states in Wi-Fi mode is intentional. State accuracy is critical, and showing potentially outdated or inconsistent information would be misleading. In particular, after sending a command there is no reliable way to predict possible side effects on related items. For example, switching on a light could affect a nearby group item. Without real-time updates, the UI might display inconsistent states. In such cases, it is preferable to show no state at all rather than an incorrect one.

See also the user manual’s [Network Access](https://next.openhab.org/docs/apps/garmin/#network-access) section for a user-facing explanation of this behavior.

### Sitemap Updates

Connect IQ does not support subscribing to sitemap updates from openHAB. Therefore, the app periodically polls the complete sitemap via a web request and rebuilds the menu structure based on the response.

The polling interval can be configured by the user in the app settings.

Polling is suspended while the app is hidden by the system. On devices with the
active/inactive app lifecycle (API level 4.2.3 and later), such as fēnix 8/9 or
Venu 3/4, returning to the watch face does not terminate the app; it keeps
running with limited access to system resources. The app pauses its request
loop in this state and resumes it, with an immediate update, when it becomes
visible again.

Polling is stopped permanently after a fatal user interface error and after a
settings change. The app settings are cached at startup and cannot be applied to
a running app, so a settings change requires the user to reopen the app.

### Commands

Commands are sent using a dedicated command web request. This request is triggered by the menu items representing the corresponding sitemap elements.

---

## Folder Structure

### Subfolder `connection/`

Contains the `ConnectionManager`, which tracks the currently available connection type (BLE vs. Wi-Fi) and manages transitions between normal, degraded (Wi-Fi), and offline modes.

For more details on connection behavior, see the [Network Access](https://next.openhab.org/docs/apps/garmin/#network-access) section of the user manual.

### Subfolder `sync-delegates/`

Implements Wi-Fi-based synchronization used when no BLE connection is available.

Sync delegates are responsible for sending commands and updating the sitemap over Wi-Fi. They coordinate communication logic while delegating actual HTTP request execution to the `web-requests` package.

#### Further Reading

- [User Manual – Network Access](https://next.openhab.org/docs/apps/garmin/#network-access)  
- [Connect IQ 3.1 Announcement](https://forums.garmin.com/developer/connect-iq/b/news-announcements/posts/connect-iq-3-1-connects-you-to-the-world)  
  Introduces SyncDelegate support and provides basic guidance on implementing Wi-Fi synchronization.
- [Connect IQ API Docs – `Toybox.Communications.SyncDelegate`](https://developer.garmin.com/connect-iq/api-docs/Toybox/Communications/SyncDelegate.html)  
  
### Subfolder `web-requests/`

Implements communication with the openHAB server using the Connect IQ SDK’s `Toybox.Communications` module.

These requests are used for both BLE communication and Wi-Fi mode. In addition to sending and receiving data, this layer is responsible for processing server responses—for example, initializing a new sitemap and forwarding it to the user interface.

#### Folder Structure

- **`web-requests/base/`**  
  Contains `BaseRequest`, the abstract base class for all HTTP requests.  
  Provides shared functionality such as applying basic authentication and validating HTTP response codes.

- **`web-requests/command/`**  
  Implements requests for sending commands to openHAB.  
  Supports both:
  - The JSON-based REST API (available in openHAB 5 and backported to openHAB 4.3.x)
  - A custom webhook integration  

- **`web-requests/sitemap/`**  
  Handles sitemap retrieval, polling, and processing.  
  `SitemapRequest` periodically fetches sitemap data from openHAB and processes the retrieved JSON representation.
  To keep the UI responsive and avoid limitations such as the Watchdog timeout for long-running code and stack size restrictions caused by recursive processing, responses are processed asynchronously and non-recursively. The work is split into small tasks that are executed by the task queue implementation.

  A response whose JSON is identical to the stored one is skipped entirely, so an unchanged sitemap neither rebuilds the data layer nor redraws the menu. The comparison with the stored JSON is itself split over several tasks (`CompareSitemapTask` and friends): the root is compared first, then each top level element in its own task, so that no single task blocks the UI for the whole sitemap. The stored timestamp is refreshed when the response is dropped, so the data does not expire. The update is applied nevertheless while the displayed model temporarily differs from the stored JSON, for example after a command has been applied locally, after returning from Wi-Fi/offline mode, or when the model was restored from stale data at startup. See `SitemapStore.noteModelDivergence()`.

  When only some top-level elements changed and the displayed model is complete and in sync, the response is applied partially: only the changed top-level elements are rebuilt and only the corresponding menu items are updated. The complete received JSON is still stored. Structural changes (root or homepage metadata, element count, `widgetId` reorder or replacement, item type changes, page additions or removals), sync mode, post-command or stale-state divergence and an incomplete model fall back to the existing full rebuild path.

#### Further Reading

- [User Manual – Sending Commands](https://next.openhab.org/docs/apps/garmin/#sending-commands-2)  
- [Connect IQ Core Topics – HTTPS Requests](https://developer.garmin.com/connect-iq/core-topics/https/)  
- [Connect IQ API Docs – `Toybox.Communications`](https://developer.garmin.com/connect-iq/api-docs/Toybox/Communications.html)
