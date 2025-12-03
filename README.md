UnexpectedMatrixPixels (UMP)A lightweight Home Assistant integration for BLE pixel matrix displays, focusing on direct control and performance rather than emulating manufacturer applications.AboutCreated because other integrations often try to emulate dedicated mobile apps, and that's not the point of a Home Assistant integration. This project focuses on raw control and flexibility.Originally developed for the IDOTMatrix 32x32, but has been tested and works great with the iPixel 16x64.Note: Both devices work excellently, provided you do not overdo the amount of frames sent (keep the FPS reasonable).CreditsBig thanks to EVERYONE involved in the community for the inspiration and groundwork.FeaturesDirect BLE Control: Uses the bleak library for reliable Bluetooth Low Energy communication.Live View: Includes a Camera entity to see exactly what is being sent to the matrix.Advanced Drawing Service: ump.draw_visuals supports:Text: Static text with custom fonts ("3x5", "5x7", "awtrix").Scrolling Text: Marquee effects.Long Text: Automatic pagination and animation for long strings.Images: Load images from local paths or URLs.Icons: Full Material Design Icons (MDI) support.Pixels: Direct individual pixel control.Time Sync: Synchronize the display's internal clock with Home Assistant.InstallationOption 1: HACS (Recommended - Auto Install)This integration is compatible with HACS (Home Assistant Community Store).Open HACS in Home Assistant.Go to Integrations > Top right menu (three dots) > Custom repositories.Paste the URL of this GitHub repository into the Repository field.Select Integration as the category.Click Add.Search for UnexpectedMatrixPixels in HACS and click Download.Restart Home Assistant.Option 2: Manual InstallationDownload the source code.Copy the ump folder into your Home Assistant custom_components directory (e.g., /config/custom_components/ump).Restart Home Assistant.ConfigurationGo to Settings -> Devices & Services.Click Add Integration.Search for UMP or wait for auto-discovery via Bluetooth.Select your device and configure the dimensions (e.g., 32x32 or 16x64).UsageService: ump.draw_visualsThis is the main service used to control the display.Example YAML:service: ump.draw_visuals
target:
  entity_id: light.display_aabbcc
data:
  fps: 10
  background: [0, 0, 0]
  elements:
    - type: text
      content: "Hello"
      x: 2
      y: 5
      color: [255, 0, 0]
      font: "5x7"
    
    - type: icon
      name: "mdi:home-assistant"
      x: 20
      y: 2
      size: 16
      color: [0, 100, 255]

    - type: textscroll
      content: "This text scrolls..."
      y: 12
      color: [0, 255, 0]
      speed: 15


Supported Element TypesTypeParameterstextcontent, x, y, color, font, spacingtextscrollcontent, y, color, font, speed, spacingtextlongcontent, x, y, color, font, speed, scroll_duration, directionimagex, y, path (local) OR url, width, heighticonname (e.g. mdi:home), x, y, size, colorpixelspixels (list of   $$ x, y, r, g, b $$  )DisclaimerThis integration pushes raw frames to the device over BLE. Performance relies heavily on your Bluetooth adapter's signal quality and congestion. If the display lags, try reducing the fps parameter in your service calls.
