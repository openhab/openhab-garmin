# Folder `fallbacks/`

Contains components responsible for fallback behavior, including error handling and the loading view. These classes ensure that the application remains robust and provides meaningful feedback when features fail or when no data is available for display.

---

## Folder Structure

### Subfolder `error/`

Manages the detection, handling, and presentation of exceptions within the app.

- `ErrorView`  
  A full-screen view used to display errors that require a full-screen presentation. Provides static helper methods to create, update, and show the view.

- `ExceptionHandler`  
  Offers reusable methods for handling exceptions throughout the app. Its primary responsibility is to determine whether an error should be presented as a toast notification or as a full-screen error view, see `CommunicationBaseException.requiresFullScreenError()`. It also contains logic to escalate toast notifications to full-screen errors after a configured number of consecutive sitemap request failures, or immediately when no sitemap is cached. Errors that cannot be presented immediately — before the initial view exists or while the app is hidden by the system — are deferred and presented by `getInitialView()` or when the app becomes visible again.

  `stopForRestart()` stops the app for good: it latches sitemap polling off, cancels in-flight requests and drops the menu. It is used for fatal user interface errors and settings changes, where only an app restart helps. Recoverable communication and parsing errors do not use it; the app keeps polling there, so it can return to the sitemap once the server responds correctly again.

- `WarningToastHandler`  
  Determines whether an error should be displayed as a toast notification and provides the functionality to show these notifications. It also provides a helper for informational toasts that are shown as direct feedback to a user action.

### Subfolder `loading/`

Implements a full-screen loading indicator that signals data is being retrieved.

This view is shown when the widget starts and no sitemap is available in storage, and when Wi-Fi mode is entered while no sitemap is cached. It remains visible until a sitemap is successfully received from the server.
