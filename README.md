# 🎨 UnexpectedMatrixPixels (UMP)

<div align="center">

**Direct, performance-focused Home Assistant integration for BLE pixel matrix displays**

Seamless control of **IDOTMatrix** and **iPixel** LED matrix displays via local Bluetooth without bridges or cloud dependencies.


[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Component-blue)](https://www.home-assistant.io/)


</div>

---

## ✨ Features

### Core Capabilities
- **Text Rendering**: Static text, scrolling text, and smart pagination for long messages
- **Visual Elements**: Full support for Material Design Icons (MDI), custom images, and raw pixel control
- **Real-time Preview**: Live camera entity showing display content synchronized with matrix
- **Performance Optimized**: Frame diffing and intelligent rendering to minimize bandwidth
- **Jinja2 Template Support**: Dynamic content generation with Home Assistant templating

### Key Advantages
- ✅ **Local Control Only**: No cloud dependency, direct Bluetooth communication
- ✅ **Zero Bridges**: Runs directly on Home Assistant host or ESPHome proxy
- ✅ **Low Latency**: Optimized BLE communication with retry mechanisms
- ✅ **Image Support**: Render local files or URLs on the matrix
- ✅ **Icon Library**: 7000+ Material Design Icons at your fingertips
- ✅ **Flexible Layout**: Precise X, Y positioning with multiple font options

---

## 🚀 Installation

### Step 1: Copy Component Files
Copy the `ump` folder to your Home Assistant configuration:
```bash
cp -r ump /config/custom_components/
```

### Step 2: Restart Home Assistant
```
Settings → Developer Tools → YAML → Restart Home Assistant
```

### Step 3: Add Integration
1. Navigate to **Settings → Devices & Services**
2. Click **Create Integration**
3. Search for **UnexpectedMatrixPixels**
4. Enter your display's MAC address
5. Specify dimensions (e.g., `16x64`, `32x32`, `64x32`)

### Step 4: Verify Setup
After setup, you'll have:
- `light.<display_name>` - Control entity
- `camera.<display_name>` - Live preview entity

---

## 📋 Requirements

```yaml
Pillow >= 10.0.0      # Image processing
bleak                  # BLE client library
bleak-retry-connector >= 1.0.0  # Connection reliability
```

**Supported Devices**:
- IDOTMatrix displays (BLE)
- iPixel displays (BLE)
- Any compatible Bluetooth LE matrix display

---

## 🎯 Quick Start

### Display Static Text
```yaml
service: ump.draw_visuals
target:
  entity_id: light.my_display
data:
  background: [0, 0, 0]
  elements:
    - type: text
      content: "Hello"
      x: 0
      y: 5
      font: "5x7"
      color: [255, 0, 0]
```

### Create Scrolling Animation
```yaml
service: ump.draw_visuals
target:
  entity_id: light.my_display
data:
  elements:
    - type: textscroll
      content: "Scrolling Message"
      y: 8
      color: [0, 255, 255]
      font: "awtrix"
      speed: 15
  fps: 10
```

### Display Material Design Icons
```yaml
service: ump.draw_visuals
target:
  entity_id: light.my_display
data:
  elements:
    - type: icon
      name: mdi:home
      x: 8
      y: 8
      size: 16
      color: [100, 255, 150]
```

### Render Local Images
```yaml
service: ump.draw_visuals
target:
  entity_id: light.my_display
data:
  elements:
    - type: image
      path: /config/www/custom_image.png
      x: 0
      y: 0
      width: 64
      height: 32
```

### Advanced: Smart Text with Pagination
```yaml
service: ump.draw_visuals
target:
  entity_id: light.my_display
data:
  elements:
    - type: textlong
      content: "This is a very long message that will scroll smoothly"
      x: 0
      y: 5
      font: "awtrix"
      color: [255, 255, 0]
      speed: 2.0
      scroll_duration: 0.5
      direction: "up"
```

### Custom Pixel Drawing
```yaml
service: ump.draw_visuals
target:
  entity_id: light.my_display
data:
  elements:
    - type: pixels
      pixels:
        - [0, 0, 255, 0, 0]      # Red pixel at (0,0)
        - [1, 0, 0, 255, 0]      # Green pixel at (1,0)
        - [2, 0, 0, 0, 255]      # Blue pixel at (2,0)
```

---

## 📚 Element Types Reference

### `text` - Static Text
Display fixed text at specified coordinates.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | ✅ | Text to display |
| `x` | int | ✅ | X position (0-63 for 64px wide) |
| `y` | int | ✅ | Y position (0-31 for 32px tall) |
| `color` | [R,G,B] | ✅ | RGB color (0-255) |
| `font` | string | ✅ | Font: `"3x5"`, `"5x7"`, or `"awtrix"` |
| `spacing` | int | ❌ | Pixel spacing between chars (default: 1) |

### `textscroll` - Scrolling Text
Text that scrolls continuously across the display.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | ✅ | Text to scroll |
| `y` | int | ✅ | Y position (vertical center) |
| `color` | [R,G,B] | ✅ | RGB color |
| `font` | string | ✅ | Font choice |
| `speed` | float | ✅ | Speed in pixels/second |
| `spacing` | int | ❌ | Char spacing |

### `textlong` - Smart Pagination & Scroll
Advanced text element with pagination and directional scrolling.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `content` | string | ✅ | Text content |
| `x` | int | ❌ | X position (default: 0) |
| `y` | int | ✅ | Y position |
| `color` | [R,G,B] | ✅ | RGB color |
| `font` | string | ✅ | Font choice |
| `speed` | float | ✅ | Hold duration (seconds) |
| `scroll_duration` | float | ✅ | Animation duration (seconds) |
| `direction` | string | ✅ | `"up"`, `"down"`, `"left"`, `"right"` |

### `icon` - Material Design Icon
Render an MDI icon at specified position.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | ✅ | MDI icon name (e.g., `"mdi:home"`) |
| `x` | int | ✅ | X position |
| `y` | int | ✅ | Y position |
| `size` | int | ✅ | Icon size in pixels |
| `color` | [R,G,B,A] | ✅ | RGBA color (A: opacity 0-255) |

### `image` - Image Rendering
Render local or remote images on the matrix.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | ✅* | Local path (use if no URL) |
| `url` | string | ✅* | Remote URL (use if no path) |
| `x` | int | ✅ | X position |
| `y` | int | ✅ | Y position |
| `width` | int | ❌ | Resize width (optional) |
| `height` | int | ❌ | Resize height (optional) |

*Either `path` OR `url` is required.

### `pixels` - Raw Pixel Data
Direct pixel-level control for custom patterns.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pixels` | array | ✅ | Array of `[x, y, r, g, b]` tuples |

---

## 🎮 Services Reference

### `ump.draw_visuals`
The primary service for rendering content on the matrix.

**Target**: Light entity with UMP integration

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `background` | [R,G,B] | [0,0,0] | Background color (black) |
| `fps` | int (1-30) | 10 | Maximum frames per second |
| `elements` | array | - | List of visual elements to render |

**Performance Tip**: Lower `fps` values provide better connection stability on unreliable networks.

### `ump.clear_display`
Instantly clear the display screen.

**Target**: Light entity with UMP integration

### `ump.sync_time`
Synchronize the display's internal clock with Home Assistant.

**Target**: Light entity with UMP integration

---

## 💡 Examples & Use Cases

### Spotify Now Playing
Real-time display of current track with album art, progress bar, and artist info.

See: `examples/spotify.yaml` for detailed implementation.

### Smart Home Status Dashboard
Display temperature, humidity, and device states with icons.

```yaml
elements:
  - type: icon
    name: mdi:thermometer
    x: 0
    y: 0
    size: 12
    color: [255, 100, 0]
  - type: text
    content: "{{ states('sensor.temperature') }}°C"
    x: 15
    y: 2
    font: "5x7"
    color: [255, 255, 255]
```

### Weather Display
Show current conditions with animated icons.

```yaml
elements:
  - type: icon
    name: "mdi:{{ state_attr('weather.home', 'icon') }}"
    x: 8
    y: 8
    size: 16
    color: [100, 200, 255]
  - type: text
    content: "{{ state_attr('weather.home', 'temperature') }}°"
    x: 28
    y: 10
    font: "awtrix"
    color: [200, 200, 200]
```

### Animation Effects
Create pulsing colors or patterns with templates.

```yaml
elements:
  - type: text
    content: "Alert!"
    x: 10
    y: 5
    font: "5x7"
    color: "{% if now().second % 2 == 0 %}[255,0,0]{% else %}[0,0,0]{% endif %}"
```

---

## 🔧 Advanced Configuration

### Jinja2 Templating
All string fields support Jinja2 templates for dynamic content:

```yaml
data:
  elements:
    - type: text
      content: "{{ now().strftime('%H:%M') }}"
      x: 0
      y: 0
      font: "5x7"
      color: [255, 255, 255]
```

### Conditional Rendering
Show different content based on conditions:

```yaml
{% if states('light.living_room') == 'on' %}
  {% set elements = [...] %}
{% endif %}
```

### Performance Tuning
- **Lower FPS**: Use 5-8 fps for stable connections with complex scenes
- **Frame Diffing**: System automatically optimizes bandwidth by sending only changed pixels
- **Batch Operations**: Combine multiple elements in single service call

---

## 🐛 Troubleshooting

### Display Not Responding
1. Check MAC address is correct: `Settings → Devices & Services → UMP`
2. Verify device is powered and in range
3. Restart Home Assistant: `Settings → Developer Tools → YAML → Restart Home Assistant`
4. Try lowering `fps` to 5-8 for better stability

### Bluetooth Connection Issues
- Ensure Home Assistant host has Bluetooth capability
- Check for interference from WiFi or other 2.4GHz devices
- Try running Home Assistant's Bluetooth debugger:
  ```
  Settings → Devices & Services → Bluetooth → Assistant settings
  ```

### Text Not Displaying Correctly
- Verify font parameter: use `"5x7"`, `"3x5"`, or `"awtrix"`
- Check text coordinates are within display bounds
- Confirm color values are in range [0-255]

### Images Not Loading
- Use absolute paths for local files: `/config/www/image.png`
- Verify image format is PNG or JPG
- Check image dimensions don't exceed display size

### Service Call Errors
- Validate YAML syntax in automation editor
- Ensure `entity_id` references an existing light entity
- Check all required parameters are provided

---

## 📦 Component Structure

```
ump/
├── __init__.py           # Integration initialization & service handlers
├── config_flow.py        # Home Assistant UI configuration
├── light.py              # Light entity implementation
├── camera.py             # Camera entity (live preview)
├── ble_client.py         # Bluetooth LE communication
├── fonts.py              # Font rendering engine
├── services.yaml         # Service definitions
├── manifest.json         # Integration metadata
└── strings.json          # Localization strings
```

---

## 🤝 Contributing

Contributions are welcome! Please feel free to:
- Report bugs via GitHub Issues
- Submit feature requests
- Create pull requests with improvements
- Share automation examples

---

## 📄 License

This project is licensed under the MIT License - see LICENSE file for details.

---

## 🙏 Acknowledgments

- **Home Assistant**: Amazing home automation platform
- **Bleak**: Excellent Python BLE library
- **Material Design Icons**: Comprehensive icon set
- **Community Contributors**: Thanks for feedback and testing

---

## 📞 Support

For issues, questions, or suggestions:
1. Check [Existing Issues](https://github.com/suchyindustries/UnexpectedMatrixPixels/issues)
2. Create a [New Issue](https://github.com/suchyindustries/UnexpectedMatrixPixels/issues/new)
3. Join Home Assistant Community discussions

---

**Made with ❤️ for the Home Assistant community**
