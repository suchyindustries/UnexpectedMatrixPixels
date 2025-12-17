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

REPLACE_CHARS = {
    'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
    'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N', 'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
}

def sanitize_text(text: str) -> str:
    for pl, en in REPLACE_CHARS.items():
        text = text.replace(pl, en)
    return text

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    mac = entry.data[CONF_MAC_ADDRESS]
    width = entry.data.get(CONF_WIDTH, DEFAULT_WIDTH)
    height = entry.data.get(CONF_HEIGHT, DEFAULT_HEIGHT)
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        client = hass.data[DOMAIN][entry.entry_id]["client"]
    else:
        client = UmpBleClient(hass, mac, width, height)
    display = IDMDisplayEntity(client, mac, entry.title, hass, width, height)
    async_add_entities([display])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "draw_visuals",
        {
            vol.Required("elements"): list,
            vol.Optional("background", default=[0, 0, 0]): list,
            vol.Optional("fps", default=10): int,  
        },
        "async_draw_visuals"
    )
    platform.async_register_entity_service("clear_display", {}, "async_clear_display")
    platform.async_register_entity_service("sync_time", {}, "async_sync_time")

class IDMDisplayEntity(LightEntity):
    def __init__(self, client: UmpBleClient, mac: str, name: str, hass: HomeAssistant, width: int, height: int) -> None:
        self._client = client
        self._mac = mac
        self._width = width
        self._height = height
        self._attr_name = name
        self._attr_unique_id = mac
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF
        self._is_on = True
        self._hass = hass
        self._anim_task = None 
        self._font_path = os.path.join(os.path.dirname(__file__), 'materialdesignicons-webfont.ttf')
        self._meta_path = os.path.join(os.path.dirname(__file__), 'materialdesignicons-webfont_meta.json')
        self._mdi_map = {} 
        self._mdi_fonts = {} 
        self._mdi_ready = False
        
        # --- CACHE (MEMOIZATION) ---
        self._char_mask_cache: Dict[Tuple[str, str], Tuple[Optional[Image.Image], int]] = {}
        
        self._hass.async_create_task(self._init_mdi())

    async def _init_mdi(self):
        if not os.path.exists(self._meta_path) or not os.path.exists(self._font_path):
            return
        try:
            def load_meta():
                with open(self._meta_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            mdi_data = await self._hass.async_add_executor_job(load_meta)
            self._mdi_map = {item['name']: item['codepoint'] for item in mdi_data}
            self._mdi_ready = True
        except Exception:
            pass

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        try:
            await self._client.set_state(True)
            await self._client.set_mode(0)
        except Exception as e:
            _LOGGER.warning(f"UMP device unavailable during turn_on: {e}")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._anim_task: self._anim_task.cancel()
        self._is_on = False
        try:
            await self._client.set_state(False)
        except Exception as e:
            _LOGGER.warning(f"UMP device unavailable during turn_off: {e}")
        self.async_write_ha_state()

    async def async_clear_display(self, **kwargs: Any) -> None:
        if self._anim_task: self._anim_task.cancel()
        try:
            await self._client.set_mode(0)
            await self._client.clear()
        except Exception as e:
            _LOGGER.warning(f"UMP device unavailable during clear_display: {e}")

    async def async_sync_time(self, **kwargs: Any) -> None:
        try:
            await self._client.sync_time()
        except Exception as e:
            _LOGGER.warning(f"UMP device unavailable during sync_time: {e}")

    async def async_draw_visuals(self, elements: list, background: list, fps: int = 10) -> None:
        # Pre-process elements first to ensure we don't fail later
        processed_elements = []
        for el in elements:
            new_el = el.copy()
            if new_el.get('type') == 'image':
                img = await self._fetch_and_process_image(new_el)
                if img: new_el['_cached_img'] = img
            
            if new_el.get('type') == 'textlong':
                lines = self._get_text_lines(
                    sanitize_text(str(new_el.get('content', ''))),
                    new_el.get('font', '5x7'),
                    int(new_el.get('spacing', 1)),
                    self._width
                )
                new_el['_cached_lines'] = lines

            processed_elements.append(new_el)

        # Try setting state/mode first
        try:
            if not self._is_on:
                await self._client.set_state(True)
                self._is_on = True
                self.async_write_ha_state()
            await self._client.set_mode(0)
        except Exception as e:
            _LOGGER.warning(f"UMP device unavailable, skipping draw_visuals: {e}")
            return # Stop processing to avoid further errors

        if self._anim_task and not self._anim_task.done():
            self._anim_task.cancel()
            self._anim_task = None
            
        # --- LOGIC FIX: DETECT IF ANIMATION IS REALLY NEEDED ---
        has_animation = False
        anim_candidates = [el for el in processed_elements if el.get('type') in ['textscroll', 'textlong']]
        
        if anim_candidates:
            for el in anim_candidates:
                if el.get('type') == 'textscroll':
                    # Textscroll always implies animation
                    has_animation = True
                    break
                elif el.get('type') == 'textlong':
                    # Textlong implies animation ONLY if multiple lines exist
                    lines = el.get('_cached_lines', [])
                    if len(lines) > 1:
                        has_animation = True
                        break
        
        if has_animation:
            self._anim_task = self._hass.async_create_task(self._animate_loop(processed_elements, background, fps))
        else:
            # STATIC FRAME LOGIC
            # Even if static, check if frame changed vs last sent frame to avoid BLE spam
            canvas = self._render_canvas_sync(processed_elements, background)
            
            if canvas.mode != 'RGB':
                canvas = canvas.convert('RGB')
            
            img_byte_arr = BytesIO()
            canvas.save(img_byte_arr, format='PNG', compress_level=0)
            new_bytes = img_byte_arr.getvalue()
            
            last_bytes = self._client.get_last_frame()
            
            if last_bytes != new_bytes:
                try:
                    await self._client.send_frame_png(canvas)
                except Exception as e:
                    _LOGGER.warning(f"UMP device disconnected while sending frame: {e}")

    async def _animate_loop(self, elements: list, background: list, fps: int):
        target_frame_time = 1.0 / max(1, min(fps, 30)) 
        
        try:
            while True:
                loop_start = time.time()
                
                canvas = self._render_canvas_sync(elements, background)
                
                if canvas.mode != 'RGB':
                    canvas = canvas.convert('RGB')
                
                img_byte_arr = BytesIO()
                canvas.save(img_byte_arr, format='PNG', compress_level=0)
                new_bytes = img_byte_arr.getvalue()
                
                last_bytes = self._client.get_last_frame()
                
                if last_bytes != new_bytes:
                    try:
                        await self._client.send_frame_png(canvas)
                    except Exception as e:
                        _LOGGER.warning(f"Error sending frame (animation): {e}")
                        # Wait a bit longer if connection failed before retrying
                        await asyncio.sleep(5.0)
                        continue

                elapsed = time.time() - loop_start
                sleep_time = max(0.01, target_frame_time - elapsed)
                await asyncio.sleep(sleep_time)
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _LOGGER.error(f"Animation loop crashed: {e}")

    def _render_canvas_sync(self, elements: list, background: list) -> Image.Image:
        bg_rgba = tuple(background)
        if len(bg_rgba) == 3:
            bg_rgba = bg_rgba + (255,)
        
        canvas = Image.new('RGBA', (self._width, self._height), bg_rgba)
        draw = ImageDraw.Draw(canvas)
        
        for el in elements:
            try:
                el_type = el.get('type')
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
                    if img.mode == 'RGBA':
                         canvas.paste(img, (x, y), img)
                    else:
                         canvas.paste(img, (x, y))
            except Exception as e:
                _LOGGER.debug(f"Error rendering element {el}: {e}")
                pass
        
        final_image = Image.new("RGB", canvas.size, (0, 0, 0))
        final_image.paste(canvas, (0, 0), mask=canvas)
        return final_image

    def _get_char_mask(self, font_name: str, char: str) -> Tuple[Optional[Image.Image], int]:
        cache_key = (font_name, char)
        if cache_key in self._char_mask_cache:
            return self._char_mask_cache[cache_key]

        img_mask = None
        advance = 0

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
            advance = 4 if font_name == 'awtrix' else (3 if font_name == '3x5' else 5)

        self._char_mask_cache[cache_key] = (img_mask, advance)
        return img_mask, advance

    def _measure_char_width(self, char: str, font_name: str) -> int:
        _, advance = self._get_char_mask(font_name, char)
        return advance

    def _measure_text_width(self, text: str, font_name: str, spacing: int) -> int:
        if not text: return 0
        width = 0
        for i, char in enumerate(text):
            width += self._measure_char_width(char, font_name)
            if i < len(text) - 1:
                width += spacing - 1 if font_name == 'awtrix' else spacing
        return width

    def _get_text_lines(self, text: str, font_name: str, spacing: int, max_width: int) -> List[str]:
        words = text.split(' ')
        lines = []
        current_line = []
        current_line_width = 0
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

    def _draw_text_element(self, canvas: Image.Image, el: Dict[str, Any]) -> None:
        content = sanitize_text(str(el.get('content', '')))
        x, y = int(el.get('x', 0)), int(el.get('y', 0))
        raw_color = el.get('color', [255, 255, 255])
        color = tuple(raw_color)
        if len(color) == 3: color = color + (255,)
        
        font_name = el.get('font', '5x7')
        spacing = int(el.get('spacing', 1))
        
        cursor_x = x
        
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
                    pass
            
            cursor_x += advance + (spacing - 1 if font_name == 'awtrix' else spacing)

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

        now = time.time()
        num_lines = len(lines)
        
        if num_lines == 1:
            draw_params = el.copy()
            draw_params['type'] = 'text'
            draw_params['content'] = lines[0]
            self._draw_text_element(canvas, draw_params)
            return

        cycle_time = speed + scroll_duration
        total_time = cycle_time * num_lines
        
        current_time_in_cycle = now % total_time
        line_idx = int(current_time_in_cycle / cycle_time)
        time_in_phase = current_time_in_cycle % cycle_time

        next_idx = (line_idx + 1) % num_lines

        draw_curr = el.copy(); draw_curr['type'] = 'text'; draw_curr['content'] = lines[line_idx]
        draw_next = el.copy(); draw_next['type'] = 'text'; draw_next['content'] = lines[next_idx]

        if time_in_phase < speed:
            draw_curr['x'] = base_x
            draw_curr['y'] = base_y
            self._draw_text_element(canvas, draw_curr)
        else:
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
        content = sanitize_text(str(el.get('content', '')))
        if not content: return
        y = int(el.get('y', 0))
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
        w, h = self._width, self._height
        
        try:
            for p in pixels:
                if len(p) >= 5:
                    draw_access[p[0], p[1]] = (p[2], p[3], p[4], p[5] if len(p) > 5 else 255)
        except Exception:
            pass
        
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
        color = tuple(c)
        if len(color) == 3: color = color + (255,)
        x, y = int(el.get('x', 0)), int(el.get('y', 0))
        
        font = self._mdi_fonts.get(size)
        if not font:
            try:
                font = ImageFont.truetype(self._font_path, size)
                self._mdi_fonts[size] = font
            except Exception: return
            
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
                except Exception: pass
        elif 'url' in el:
            try:
                session = async_get_clientsession(self._hass)
                async with session.get(el['url'], timeout=10) as response:
                    if response.status == 200:
                        image_data = await response.read()
            except Exception: pass
        if image_data:
            try:
                img = Image.open(BytesIO(image_data)).convert("RGBA")
                w, h = el.get('width'), el.get('height')
                if w and h:
                    img = img.resize((int(w), int(h)), Image.Resampling.NEAREST)
                return img
            except Exception: pass
        return None
