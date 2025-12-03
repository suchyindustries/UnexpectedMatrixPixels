# Unexpected Matrix Pixels (UMP)

UMP is a alternative to original apps.
Home Assistant integration for controlling BLE LED matrix displays via local Bluetooth. It requires no bridges and runs directly on the HA host or ESPHome Proxy
Features
 * Core: Controls text, scrolling, icons (MDI), images, and raw pixels - with jinga2
 * Smart Rendering: Supports pagination (textlong) and frame diffing for performance.
 * Live Preview: Exposes a Camera entity showing the real-time content of the matrix.
Setup
 * Copy the ump folder to /config/custom_components/ and restart Home Assistant. 
 * Add UMP Display via Settings > Devices & Services.
 * Enter the MAC address and dimensions (e.g., 16x64, 32x32).
 * The integration creates two entities:
   * light.display_name (Control)
   * camera.display_name (Live Preview)
Usage
Use the ump.draw_visuals service to set content.
Parameters:
 * entity_id: Target light entity.
 * elements: Ordered list of visual items.
 * background: RGB list (default [0,0,0]).
 * fps: Limit frame rate (1-30, default 10). Lower this if connection is unstable.
Visual Elements
Define these in the elements list:
 * Static Text: type: text with content, x, y, font ("awtrix"/"5x7"), color.
 * Ticker: type: textscroll with speed (px/sec).
 * Smart Text (textlong): Splits long text into pages.
   * speed: Duration to hold text static (seconds).
   * scroll_duration: Animation time (seconds).
   * direction: up, down, left, right.
 * Graphics:
   * icon: MDI name (e.g., mdi:home), size, color.
   * image: Local path or url. 
   * pixels: Array of [x, y, r, g, b].

Check examples!

TODO:
- working brightness

