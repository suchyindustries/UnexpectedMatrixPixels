from __future__ import annotations
import logging
import voluptuous as vol
import os
import asyncio
import time
import json
import aiohttp
from io import BytesIO
from typing import Any, List, Dict, Optional, Tuple

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from PIL import Image, ImageDraw, ImageFont

from .const import DOMAIN, CONF_MAC_ADDRESS, CONF_WIDTH, CONF_HEIGHT, DEFAULT_WIDTH, DEFAULT_HEIGHT
from .ble_client import UmpBleClient
from .fonts import FONT_3X5_DATA, FONT_5X7_DATA, AWTRIX_BITMAPS, AWTRIX_GLYPHS

_LOGGER = logging.getLogger(__name__)

# Optimization: Compiled translation map is slightly faster
REPLACE_CHARS = {
    'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
    'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N', 'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
}
TRANS_TABLE = str.maketrans(REPLACE_CHARS)

def sanitize_text(text: str) -> str:
    """Replaces Polish characters with ASCII equivalents."""
    return text.translate(TRANS_TABLE)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    mac = entry.data[CONF_MAC_ADDRESS]
    width = entry.data.get(CONF_WIDTH, DEFAULT_WIDTH)
    height = entry.data.get(CONF_HEIGHT, DEFAULT_HEIGHT)
    
    # Better safety check using .get()
    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.get(entry.entry_id)

    if entry_data:
        client = entry_data["client"]
    else:
        # Fallback if setup order was unusual, though logically shouldn't happen if __init__ passed
        client = UmpBleClient(hass, mac, width, height)

    # Renamed class for consistency
    display = UmpDisplayEntity(client, mac, entry.title, hass, width, height)
    async_add_entities([display])

    platform = entity_platform.async_get_current_platform()
    
    # Schema definitions extracted for clarity
    DRAW_SCHEMA = {
        vol.Required("elements"): list,
        vol.Optional("background", default=[0, 0, 0]): list,
        vol.Optional("fps", default=10): int,  
    }
    
    platform.async_register_entity_service("draw_matrix", DRAW_SCHEMA, "async_draw_matrix")
    platform.async_register_entity_service("clear_display", {}, "async_clear_display")
    platform.async_register_entity_service("sync_time", {}, "async_sync_time")

class UmpDisplayEntity(LightEntity):
    def __init__(self, client: UmpBleClient, mac: str, name: str, hass: HomeAssistant, width: int, height: int) -> None:
        self._client = client
        self._mac = mac
        self._width = width
        self._height = height
        self._attr_name = name
        self._attr_unique_id = mac
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._is_on = True
        self._brightness = 255
        self._hass = hass
        self._anim_task = None 
        
        self._current_mode = None 
        self._ble_lock = asyncio.Lock()

        # Paths
        base_path = os.path.dirname(__file__)
        self._font_path = os.path.join(base_path, 'materialdesignicons-webfont.ttf')
        self._meta_path = os.path.join(base_path, 'materialdesignicons-webfont_meta.json')
        
        self._mdi_map = {} 
        self._mdi_fonts = {} 
        self._mdi_ready = False
        
        self._char_mask_cache: Dict[Tuple[str, str], Tuple[Optional[Image.Image], int]] = {}
        
        self._hass.async_create_task(self._init_mdi())

    async def _init_mdi(self):
        """Load MDI meta data in executor to avoid blocking loop."""
        if not os.path.exists(self._meta_path) or not os.path.exists(self._font_path):
            _LOGGER.warning("MDI font files missing.")
            return
        try:
            def load_meta():
                with open(self._meta_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            mdi_data = await self._hass.async_add_executor_job(load_meta)
            self._mdi_map = {item['name']: item['codepoint'] for item in mdi_data}
            self._mdi_ready = True
        except Exception as e:
            _LOGGER.error(f"Failed to load MDI metadata: {e}")

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int:
        return self._brightness

    def _apply_brightness_to_color(self, color: tuple) -> tuple:
        """Scales color proportionally to brightness."""
        if self._brightness == 255:
            return color
            
        scale = self._brightness / 255.0
        # Optimization: List comprehension is slightly cleaner
        return tuple(int(c * scale) for c in color[:3]) + (color[3:] if len(color) > 3 else ())

    async def _show_brightness_indicator(self):
        """Shows brightness indicator for 2 seconds."""
        percent = int((self._brightness / 255) * 100)
        bg_val = int((self._brightness / 255) * 255)
        bg_color = (bg_val, bg_val, bg_val, 255)
        
        # Determine text color for contrast
        text_col_val = 0 if bg_val > 127 else 255
        text_color = [text_col_val, text_col_val, text_col_val]
        
        temp_canvas = Image.new('RGBA', (self._width, self._height), bg_color)
        
        text = f"{percent}"
        font_name = '5x7'
        spacing = 1
        
        text_width = self._measure_text_width(text, font_name, spacing)
        x = (self._width - text_width) // 2
        y = (self._height - 7) // 2
        
        el = {
            'type': 'text',
            'content': text,
            'x': x,
            'y': y,
            'color': text_color,
            'font': font_name,
            'spacing': spacing
        }
        
        self._draw_text_element_raw(temp_canvas, el)
        
        final = Image.new("RGB", temp_canvas.size, (bg_val, bg_val, bg_val))
        final.paste(temp_canvas, (0, 0), mask=temp_canvas)
        
        async with self._ble_lock:
            try:
                await self._client.send_frame_png(final)
            except Exception as e:
                _LOGGER.debug(f"Error showing brightness indicator: {e}")
        
        await asyncio.sleep(2.0)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if "brightness" in kwargs:
            new_brightness = kwargs["brightness"]
            if new_brightness != self._brightness:
                self._brightness = new_brightness
                self._hass.async_create_task(self._show_brightness_indicator())
        
        self._is_on = True
        async with self._ble_lock:
            try:
                await self._client.set_state(True)
                await self._client.set_mode(0)
                self._current_mode = 0
            except Exception as e:
                _LOGGER.warning(f"UMP unavailable during turn_on: {e}")
                self._current_mode = None 
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._anim_task: self._anim_task.cancel()
        self._is_on = False
        async with self._ble_lock:
            try:
                await self._client.set_state(False)
                self._current_mode = None 
            except Exception as e:
                _LOGGER.warning(f"UMP unavailable during turn_off: {e}")
        self.async_write_ha_state()

    async def async_clear_display(self, **kwargs: Any) -> None:
        if self._anim_task: self._anim_task.cancel()
        async with self._ble_lock:
            try:
                if self._current_mode != 0:
                    await self._client.set_mode(0)
                    self._current_mode = 0
                await self._client.clear()
            except Exception as e:
                _LOGGER.warning(f"UMP unavailable during clear_display: {e}")
                self._current_mode = None

    async def async_sync_time(self, **kwargs: Any) -> None:
        async with self._ble_lock:
            try:
                await self._client.sync_time()
            except Exception as e:
                _LOGGER.warning(f"UMP unavailable during sync_time: {e}")

    async def async_draw_matrix(self, elements: list, background: list, fps: int = 10) -> None:
        # --- PRE-PROCESSING ---
        # Perform explicit sanitation here once, instead of in every render loop
        processed_elements = []
        for el in elements:
            new_el = el.copy()
            
            # Sanitize content early
            if 'content' in new_el:
                new_el['content'] = sanitize_text(str(new_el['content']))

            if new_el.get('type') == 'image':
                img = await self._fetch_and_process_image(new_el)
                if img: new_el['_cached_img'] = img
            
            if new_el.get('type') == 'textlong':
                # Content is already sanitized above
                lines = self._get_text_lines(
                    new_el['content'],
                    new_el.get('font', '5x7'),
                    int(new_el.get('spacing', 1)),
                    self._width
                )
                new_el['_cached_lines'] = lines

            processed_elements.append(new_el)

        # 1. Ensure powered on
        if not self._is_on:
            async with self._ble_lock:
                try:
                    await self._client.set_state(True)
                    self._is_on = True
                    self.async_write_ha_state()
                except Exception as e:
                    _LOGGER.warning(f"UMP unavailable (cannot turn on): {e}")
                    return

        # 2. Ensure Mode 0 (Optimistic)
        async with self._ble_lock:
            if self._current_mode != 0:
                try:
                    await self._client.set_mode(0)
                    self._current_mode = 0
                except Exception as e:
                    _LOGGER.debug(f"Failed to set mode 0 (continuing anyway): {e}")
                    self._current_mode = 0 

        if self._anim_task and not self._anim_task.done():
            self._anim_task.cancel()
            self._anim_task = None
            
        # Check for animation requirements
        has_animation = False
        for el in processed_elements:
            etype = el.get('type')
            if etype == 'textscroll':
                has_animation = True
                break
            elif etype == 'textlong':
                if len(el.get('_cached_lines', [])) > 1:
                    has_animation = True
                    break
        
        if has_animation:
            self._anim_task = self._hass.async_create_task(self._animate_loop(processed_elements, background, fps))
        else:
            # Static Frame
            await self._render_and_send(processed_elements, background)

    async def _render_and_send(self, elements: list, background: list):
        """Helper to reduce code duplication between static and anim loop."""
        # Run CPU-bound render in executor? 
        # For small matrices (32x8), sync render is usually fine (<5ms). 
        # If matrix grows, move this to executor.
        canvas = self._render_canvas_sync(elements, background)
        if canvas.mode != 'RGB':
            canvas = canvas.convert('RGB')
        
        img_byte_arr = BytesIO()
        canvas.save(img_byte_arr, format='PNG', compress_level=0)
        new_bytes = img_byte_arr.getvalue()
        
        last_bytes = self._client.get_last_frame()
        
        if last_bytes != new_bytes:
            async with self._ble_lock:
                try:
                    await self._client.send_frame_png(canvas)
                except Exception as e:
                    _LOGGER.debug(f"UMP disconnected while sending frame: {e}")

    async def _animate_loop(self, elements: list, background: list, fps: int):
        target_frame_time = 1.0 / max(1, min(fps, 30)) 
        
        try:
            while True:
                loop_start = time.time()
                await self._render_and_send(elements, background)
                elapsed = time.time() - loop_start
                sleep_time = max(0.01, target_frame_time - elapsed)
                await asyncio.sleep(sleep_time)
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _LOGGER.error(f"Animation loop crashed: {e}")

    def _render_canvas_sync(self, elements: list, background: list) -> Image.Image:
        bg_rgba = tuple(self._apply_brightness_to_color(tuple(background)))
        if len(bg_rgba) == 3:
            bg_rgba = bg_rgba + (255,)
        
        canvas = Image.new('RGBA', (self._width, self._height), bg_rgba)
        draw = ImageDraw.Draw(canvas)
        
        for el in elements:
            try:
                el_type = el.get('type')
                # Content is already sanitized in async_draw_matrix pre-processing
                
                if el_type == 'text':
                    self._draw_text_element(canvas, el)
                elif el_type == 'textscroll':
                    self._draw_textscroll_element(canvas, draw, el)
                elif el_type == 'textlong':
                    self._draw_textlong_element(canvas, el)
                elif el_type == 'pixels':
                    self._draw_pixels_element(canvas, el)
                elif el_type == 'icon':
                    self._draw_mdi_element(canvas, el)
                elif el_type == 'image' and '_cached_img' in el:
                    x, y = int(el.get('x', 0)), int(el.get('y', 0))
                    img = el['_cached_img']
                    img = self._apply_brightness_to_image(img)
                    if img.mode == 'RGBA':
                         canvas.paste(img, (x, y), img)
                    else:
                         canvas.paste(img, (x, y))
            except Exception as e:
                # Log debug instead of pass
                _LOGGER.debug(f"Error rendering element {el.get('type')}: {e}")
        
        final_image = Image.new("RGB", canvas.size, (0, 0, 0))
        final_image.paste(canvas, (0, 0), mask=canvas)
        return final_image

    def _apply_brightness_to_image(self, img: Image.Image) -> Image.Image:
        if self._brightness == 255:
            return img
        
        scale = self._brightness / 255.0
        # Image.eval is much faster than python pixel loops
        # However, for full color control, point operations are preferred
        # Keeping pixel loop for now as it handles RGBA logic specifically for the matrix
        img_array = img.copy()
        pixels = img_array.load()
        
        w, h = img_array.size
        for y in range(h):
            for x in range(w):
                if img_array.mode == 'RGBA':
                    r, g, b, a = pixels[x, y]
                    pixels[x, y] = (int(r * scale), int(g * scale), int(b * scale), a)
                elif img_array.mode == 'RGB':
                    r, g, b = pixels[x, y]
                    pixels[x, y] = (int(r * scale), int(g * scale), int(b * scale))
        
        return img_array

    def _get_char_mask(self, font_name: str, char: str) -> Tuple[Optional[Image.Image], int]:
        cache_key = (font_name, char)
        if cache_key in self._char_mask_cache:
            return self._char_mask_cache[cache_key]

        img_mask = None
        advance = 0

        # Optimization: Flattened logic slightly for readability
        if font_name == 'awtrix':
            code = ord(char)
            if 32 <= code <= 126:
                glyph_idx = code - 32
                if glyph_idx < len(AWTRIX_GLYPHS):
                    (bo, w, h, adv, xo, yo) = AWTRIX_GLYPHS[glyph_idx]
                    advance = adv
                    if w > 0 and h > 0:
                        img_mask = Image.new('1', (w, h), 0)
                        bits = 0
                        bit_counter = 0
                        current_bitmap_idx = bo
                        for yy in range(h):
                            for xx in range(w):
                                if (bit_counter & 7) == 0:
                                    if current_bitmap_idx < len(AWTRIX_BITMAPS):
                                        bits = AWTRIX_BITMAPS[current_bitmap_idx]
                                        current_bitmap_idx += 1
                                    else:
                                        bits = 0
                                bit_counter += 1
                                if bits & 0x80:
                                    img_mask.putpixel((xx, yy), 1)
                                bits <<= 1
            else:
                advance = 4
        else:
            # Standard pixel fonts
            if font_name == '3x5':
                font_data = FONT_3X5_DATA; char_w = 3; char_h = 5; stride = 3
            else:
                font_data = FONT_5X7_DATA; char_w = 5; char_h = 7; stride = 7
            
            advance = char_w
            code = ord(char)
            if code * stride < len(font_data):
                img_mask = Image.new('1', (char_w, char_h), 0)
                offset = code * stride
                for col in range(char_w):
                    if col >= stride: break
                    byte = font_data[offset + col]
                    for row in range(8):
                        if row >= char_h: break
                        if (byte >> row) & 1:
                            img_mask.putpixel((col, row), 1)

        if img_mask is None:
            # Fallback width
            advance = 4 if font_name == 'awtrix' else (3 if font_name == '3x5' else 5)

        self._char_mask_cache[cache_key] = (img_mask, advance)
        return img_mask, advance

    def _measure_char_width(self, char: str, font_name: str) -> int:
        _, advance = self._get_char_mask(font_name, char)
        return advance

    def _measure_text_width(self, text: str, font_name: str, spacing: int) -> int:
        if not text: return 0
        width = 0
        extra_space = spacing - 1 if font_name == 'awtrix' else spacing
        for i, char in enumerate(text):
            width += self._measure_char_width(char, font_name)
            if i < len(text) - 1:
                width += extra_space
        return width

    def _get_text_lines(self, text: str, font_name: str, spacing: int, max_width: int) -> List[str]:
        words = text.split(' ')
        lines = []
        current_line = []
        current_line_width = 0
        
        # Calculate space width once
        space_width = self._measure_char_width(' ', font_name) 
        actual_space_px = space_width + (spacing - 1 if font_name == 'awtrix' else spacing)

        for word in words:
            word_width = self._measure_text_width(word, font_name, spacing)
            
            if not current_line:
                current_line.append(word)
                current_line_width = word_width
            else:
                new_width = current_line_width + actual_space_px + word_width
                if new_width <= max_width:
                    current_line.append(word)
                    current_line_width = new_width
                else:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                    current_line_width = word_width
        
        if current_line:
            lines.append(" ".join(current_line))
            
        return lines

    def _draw_text_element_raw(self, canvas: Image.Image, el: Dict[str, Any]) -> None:
        """Draw text without brightness scaling (for indicator)."""
        content = str(el.get('content', '')) # Already sanitized if coming from processed list
        x, y = int(el.get('x', 0)), int(el.get('y', 0))
        raw_color = el.get('color', [255, 255, 255])
        color = tuple(raw_color)
        if len(color) == 3: color = color + (255,)
        
        font_name = el.get('font', '5x7')
        spacing = int(el.get('spacing', 1))
        
        self._draw_char_loop(canvas, content, x, y, color, font_name, spacing)

    def _draw_text_element(self, canvas: Image.Image, el: Dict[str, Any]) -> None:
        content = str(el.get('content', ''))
        x, y = int(el.get('x', 0)), int(el.get('y', 0))
        raw_color = el.get('color', [255, 255, 255])
        
        color = tuple(self._apply_brightness_to_color(tuple(raw_color)))
        if len(color) == 3: color = color + (255,)
        
        font_name = el.get('font', '5x7')
        spacing = int(el.get('spacing', 1))

        self._draw_char_loop(canvas, content, x, y, color, font_name, spacing)

    def _draw_char_loop(self, canvas, content, x, y, color, font_name, spacing):
        """Helper to avoid code duplication in draw_text logic."""
        cursor_x = x
        extra_space = spacing - 1 if font_name == 'awtrix' else spacing

        for char in content:
            mask, advance = self._get_char_mask(font_name, char)
            
            if mask:
                draw_y = y
                draw_x = cursor_x
                
                if font_name == 'awtrix':
                    code = ord(char)
                    if 32 <= code <= 126:
                        glyph_idx = code - 32
                        if glyph_idx < len(AWTRIX_GLYPHS):
                            (_, _, _, _, xo, yo) = AWTRIX_GLYPHS[glyph_idx]
                            draw_x += xo
                            draw_y += (5 + yo)
                
                try:
                    canvas.paste(color, (draw_x, draw_y), mask)
                except Exception:
                    pass # Mask paste error is usually negligible
            
            cursor_x += advance + extra_space

    def _draw_textlong_element(self, canvas, el: Dict[str, Any]) -> None:
        lines = el.get('_cached_lines', [])
        if not lines: return

        base_x = int(el.get('x', 0))
        base_y = int(el.get('y', 0))
        speed = float(el.get('speed', 2.0)) 
        scroll_duration = float(el.get('scroll_duration', 0.5))
        direction = el.get('direction', 'up') 
        
        font_name = el.get('font', '5x7')
        if font_name == '3x5': line_h = 6
        elif font_name == '5x7': line_h = 8
        else: line_h = 8

        num_lines = len(lines)
        if num_lines == 1:
            # Fallback to simple text draw
            draw_params = el.copy()
            draw_params['type'] = 'text'
            draw_params['content'] = lines[0]
            self._draw_text_element(canvas, draw_params)
            return

        now = time.time()
        cycle_time = speed + scroll_duration
        total_time = cycle_time * num_lines
        
        current_time_in_cycle = now % total_time
        line_idx = int(current_time_in_cycle / cycle_time)
        time_in_phase = current_time_in_cycle % cycle_time

        next_idx = (line_idx + 1) % num_lines

        # Prepare temporary elements for current and next line
        draw_curr = el.copy(); draw_curr['type'] = 'text'; draw_curr['content'] = lines[line_idx]
        draw_next = el.copy(); draw_next['type'] = 'text'; draw_next['content'] = lines[next_idx]

        if time_in_phase < speed:
            draw_curr['x'] = base_x
            draw_curr['y'] = base_y
            self._draw_text_element(canvas, draw_curr)
        else:
            # Animation Phase
            anim_progress = (time_in_phase - speed) / scroll_duration
            if anim_progress > 1.0: anim_progress = 1.0
            
            offset_y = 0; offset_x = 0
            
            if direction == 'up':
                offset_y = int(anim_progress * line_h)
                draw_curr['y'] = base_y - offset_y
                draw_next['y'] = base_y + line_h - offset_y
                draw_curr['x'] = base_x; draw_next['x'] = base_x

            elif direction == 'down':
                offset_y = int(anim_progress * line_h)
                draw_curr['y'] = base_y + offset_y
                draw_next['y'] = base_y - line_h + offset_y
                draw_curr['x'] = base_x; draw_next['x'] = base_x

            elif direction == 'left':
                offset_x = int(anim_progress * self._width)
                draw_curr['x'] = base_x - offset_x
                draw_next['x'] = base_x + self._width - offset_x
                draw_curr['y'] = base_y; draw_next['y'] = base_y

            elif direction == 'right':
                offset_x = int(anim_progress * self._width)
                draw_curr['x'] = base_x + offset_x
                draw_next['x'] = base_x - self._width + offset_x
                draw_curr['y'] = base_y; draw_next['y'] = base_y

            self._draw_text_element(canvas, draw_curr)
            self._draw_text_element(canvas, draw_next)

    def _draw_textscroll_element(self, canvas, draw, el: Dict[str, Any]) -> None:
        content = str(el.get('content', ''))
        if not content: return
        
        font_name = el.get('font', '5x7')
        spacing = int(el.get('spacing', 1))
        speed = int(el.get('speed', 10))
        text_width = self._measure_text_width(content, font_name, spacing)
        
        if text_width < 1: return
        total_distance = self._width + text_width
        offset = (time.time() * speed) % total_distance
        x = int(self._width - offset)
        
        temp_el = el.copy()
        temp_el['type'] = 'text'
        temp_el['content'] = content 
        temp_el['x'] = x
        self._draw_text_element(canvas, temp_el)

    def _draw_pixels_element(self, canvas: Image.Image, el: Dict[str, Any]) -> None:
        pixels = el.get('pixels', [])
        if not pixels: return

        layer = Image.new('RGBA', (self._width, self._height), (0, 0, 0, 0))
        draw_access = layer.load()
        
        try:
            for p in pixels:
                if len(p) >= 5:
                    pixel_color = self._apply_brightness_to_color((p[2], p[3], p[4]))
                    alpha = p[5] if len(p) > 5 else 255
                    draw_access[p[0], p[1]] = (pixel_color[0], pixel_color[1], pixel_color[2], alpha)
        except Exception as e:
            # Helpful debug if pixel data format is wrong
            _LOGGER.debug(f"Pixel draw error: {e}")
        
        canvas.alpha_composite(layer)

    def _draw_mdi_element(self, canvas, el: Dict[str, Any]):
        if not self._mdi_ready: return
        
        raw_name = str(el.get('name', 'mdi:help'))
        icon_name = raw_name[4:] if raw_name.startswith("mdi:") else raw_name
        
        hex_code = self._mdi_map.get(icon_name)
        if not hex_code: return
        
        icon_char = chr(int(hex_code, 16))
        size = int(el.get('size', 16))
        c = el.get('color', [255, 255, 255])
        
        color = tuple(self._apply_brightness_to_color(tuple(c)))
        if len(color) == 3: color = color + (255,)
        
        x, y = int(el.get('x', 0)), int(el.get('y', 0))
        
        font = self._mdi_fonts.get(size)
        if not font:
            try:
                font = ImageFont.truetype(self._font_path, size)
                self._mdi_fonts[size] = font
            except Exception as e: 
                # Log error only once per missing size to avoid spam, or debug level
                _LOGGER.debug(f"Could not load font size {size}: {e}")
                return
            
        layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.text((x, y), icon_char, font=font, fill=color)
        canvas.alpha_composite(layer)

    async def _fetch_and_process_image(self, el: Dict[str, Any]) -> Optional[Image.Image]:
        image_data = None
        if 'path' in el:
            path = el['path']
            if self._hass.config.is_allowed_path(path):
                try:
                    def load_local():
                        with open(path, "rb") as f: return f.read()
                    image_data = await self._hass.async_add_executor_job(load_local)
                except Exception as e:
                    _LOGGER.debug(f"Failed to load local image {path}: {e}")
        elif 'url' in el:
            try:
                session = async_get_clientsession(self._hass)
                async with session.get(el['url'], timeout=10) as response:
                    if response.status == 200:
                        image_data = await response.read()
            except Exception as e:
                _LOGGER.debug(f"Failed to download image {el['url']}: {e}")
        
        if image_data:
            try:
                img = Image.open(BytesIO(image_data)).convert("RGBA")
                w, h = el.get('width'), el.get('height')
                if w and h:
                    img = img.resize((int(w), int(h)), Image.Resampling.NEAREST)
                return img
            except Exception as e:
                _LOGGER.debug(f"Failed to process image data: {e}")
        return None
