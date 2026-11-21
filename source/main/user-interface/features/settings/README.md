# Folder `settings`

Implements the _Settings_ feature.

Currently provides the following information and actions:

- App version  
- Connect IQ version  
- openHAB server URL  
- Sitemap name  
- Current connection mode (Phone/BLE, Wi-Fi, Offline)  
- Timestamp of the last sitemap update  
- Manual sitemap update over Wi-Fi  

Because Wi-Fi mode does not automatically poll for structural sitemap changes, updates must be triggered manually when operating without a BLE connection.  
See [No Polling of Sitemap Changes and States](https://next.openhab.org/docs/apps/garmin/#no-polling-of-sitemap-changes-and-states) in the user manual for details and screenshots.

---

## Platform-Specific Behavior

- **Button-based devices**  
  On most button-based devices (for example Fenix 7 Pro or Epix 2 Pro), the _Settings_ menu appears as a parallel menu to the homepage. It is accessed by scrolling beyond the title or footer of the _Homepage_ menu, mimicking the system settings access pattern.  

- **Touch-based devices, and button-based Edge devices**  
  Due to more limited gesture support, and because of a firmware issue with the page-scroll delegates on button-based Edge devices, the _Settings_ menu is implemented as a regular menu item within the _Homepage_.

Device-specific behavior is defined in the `monkey.jungle` build file. Exclusion annotations (also used in input delegates) ensure that the correct implementation is compiled per device.

---

## Folder Structure

### Subfolder `manager/`

Contains `SettingsMenuManager`, which controls the lifecycle and presentation of the settings menu.

It maintains singleton instances of the menu and its input delegate, tracks whether the menu is currently visible, and coordinates transitions between the homepage and the settings view. It also determines focus behavior based on slide direction and input type, and manages pushing and popping the view on the `ViewStack`.

### Subfolder `menu/`

Contains the `SettingsMenu` implementation based on `BaseMenu`, along with the corresponding input delegates for handling user interaction.

### Subfolder `menu-items/`

Contains the individual menu items displayed within the _Settings_ menu.
