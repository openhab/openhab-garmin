# Folder `fallbacks/`

Contains components responsible for fallback behavior, including error handling and the loading view. These classes ensure that the application remains robust and provides meaningful feedback when features fail or when no data is available for display.

---

## Folder Structure

### Subfolder `error-handling/`

Manages the detection, handling, and presentation of exceptions within the app.

- `ErrorView`  
  A full-screen view used to display fatal exceptions. Provides static helper methods to show or hide the view.

- `ExceptionHandler`  
  Offers reusable methods for handling exceptions throughout the app. Its primary responsibility is to determine whether an error should be presented as a toast notification for non-fatal issues or as a full-screen error view for fatal ones. It also contains logic to escalate non-fatal errors to fatal errors if they persist beyond a defined duration. Errors that cannot be presented immediately — before the initial view exists or while the app is hidden by the system — are deferred and presented by `getInitialView()` or when the app becomes visible again.

- `WarningToastHandler`  
  Determines whether a non-fatal error should be displayed as a toast notification and provides the functionality to show these notifications.

### Subfolder `loading-view/`

Implements a full-screen loading indicator that signals data is being retrieved.

This view is shown when the widget starts and no sitemap is available in storage. It remains visible until a sitemap is successfully received from the server.
