from __future__ import annotations
import logging
import voluptuous as vol
import os
import asyncio
import json
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

# Character mapping for sanitization (Polish to ASCII)
CHAR_MAP = {
    'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
    'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N', 'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
}
TRANS_TABLE = str.maketrans(CHAR_MAP)

def sanitize_text(text: str) -> str:
    """Normalize text to ASCII."""
    return text.translate(TRANS_TABLE)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    mac = entry.data[CONF_MAC_ADDRESS]
    width = entry.data.get(CONF_WIDTH, DEFAULT_WIDTH)
    height = entry.data.get(CONF_HEIGHT, DEFAULT_HEIGHT)
    
    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.get(entry.entry_id)

    client = entry_data["client"] if entry_data else UmpBleClient(hass, mac, width, height)
    display = UmpDisplayEntity(client, mac, entry.title, hass, width, height)
    async_add_entities([display])

    platform = entity_platform.async_get_current_platform()
    
    DRAW_MATRIX_SCHEMA = {
        vol.Required("elements"): list,
        vol.Optional("background", default=[0, 0, 0]): list,
        vol.Optional("transition"): {
            vol.Optional("type", default="slide_up"): str,
            vol.Optional("duration", default=1.0): vol.Coerce(float),
            vol.Optional("fps", default=20): int,
        }
    }
    
    platform.async_register_entity_service("draw_matrix", DRAW_MATRIX_SCHEMA, "async_draw_matrix")
    platform.async_register_entity_service("clear_display", {}, "async_clear_display")

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
        self._last_image: Optional[Image.Image] = None

        base_path = os.path.dirname(__file__)
        self._font_path = os.path.join(base_path, 'materialdesignicons-webfont.ttf')
        self._meta_path = os.path.join(base_path, 'materialdesignicons-webfont_meta.json')
        
        self._mdi_map = {} 
        self._mdi_fonts = {} 
        self._mdi_ready = False
        self._char_mask_cache = {}
        
        self._hass.async_create_task(self._init_mdi())

    async def _init_mdi(self):
        """Load MDI icon metadata in background."""
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
        if self._brightness == 255:
            return color
        scale = self._brightness / 255.0
        return tuple(int(c * scale) for c in color[:3]) + (color[3:] if len(color) > 3 else ())

    async def async_turn_on(self, **kwargs: Any) -> None:
        if "brightness" in kwargs:
            self._brightness = kwargs["brightness"]
        self._is_on = True
        async with self._ble_lock:
            try:
                await self._client.set_state(True)
                await self._client.set_mode(0)
                self._current_mode = 0
            except Exception as e:
                _LOGGER.warning(f"Device unreachable during turn_on: {e}")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._anim_task: self._anim_task.cancel()
        self._is_on = False
        async with self._ble_lock:
            try:
                await self._client.set_state(False)
            except Exception as e:
                _LOGGER.warning(f"Device unreachable during turn_off: {e}")
        self.async_write_ha_state()

    async def async_clear_display(self, **kwargs: Any) -> None:
        if self._anim_task: self._anim_task.cancel()
        async with self._ble_lock:
            try:
                if self._current_mode != 0:
                    await self._client.set_mode(0)
                    self._current_mode = 0
                await self._client.clear()
                self._last_image = Image.new('RGB', (self._width, self._height), (0,0,0))
            except Exception as e:
                _LOGGER.warning(f"Device unreachable during clear: {e}")

    async def async_draw_matrix(self, elements: list, background: list = None, transition: dict = None) -> None:
        """Main service to render elements on matrix."""
        if background is None: background = [0, 0, 0]
        if self._anim_task and not self._anim_task.done():
            self._anim_task.cancel()

        await self._ensure_display_ready()
        
        processed_elements = await self._preprocess_elements(elements)
        img_to = self._render_canvas_sync(processed_elements, background)

        if transition:
            trans_type = transition.get("type", "slide_up")
            duration = float(transition.get("duration", 1.0))
            fps = int(transition.get("fps", 20))
            img_from = self._last_image or Image.new('RGB', (self._width, self._height), (0,0,0))
            
            self._anim_task = self._hass.async_create_task(
                self._animate_transition(img_from, img_to, duration, trans_type, fps, background)
            )
        else:
            await self._send_frame(img_to)
            self._last_image = img_to

    async def _animate_transition(self, img_from: Image.Image, img_to: Image.Image, 
                                  duration: float, trans_type: str, fps: int, bg_color: list):
        steps = max(int(duration * fps), 1)
        delay = 1.0 / fps
        try:
            for i in range(steps + 1):
                progress = i / steps
                frame = self._render_transition_frame(img_from, img_to, progress, trans_type, bg_color)
                async with self._ble_lock:
                    try:
                        await self._client.send_frame_png(frame)
                    except: pass
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass
        finally:
            self._last_image = img_to

    def _render_transition_frame(self, img_from: Image.Image, img_to: Image.Image, 
                               progress: float, anim_type: str, bg_color: list) -> Image.Image:
        bg = tuple(bg_color[:3]) if len(bg_color) >= 3 else (0,0,0)
        canvas = Image.new('RGB', (self._width, self._height), bg)
        w, h = self._width, self._height
        p = 1 - (1 - progress)**2 # Ease out quad

        if anim_type == "slide_up":
            offset = int(h * p)
            canvas.paste(img_from, (0, -offset))
            canvas.paste(img_to, (0, h - offset))
        elif anim_type == "slide_down":
            offset = int(h * p)
            canvas.paste(img_from, (0, offset))
            canvas.paste(img_to, (0, -h + offset))
        elif anim_type == "slide_left":
            offset = int(w * p)
            canvas.paste(img_from, (-offset, 0))
            canvas.paste(img_to, (w - offset, 0))
        elif anim_type == "slide_right":
            offset = int(w * p)
            canvas.paste(img_from, (offset, 0))
            canvas.paste(img_to, (-w + offset, 0))
        elif anim_type == "dissolve":
            f, t = img_from.convert('RGBA'), img_to.convert('RGBA')
            canvas.paste(Image.blend(f, t, p), (0, 0))
        else:
            return img_to if p > 0.5 else img_from
        return canvas

    async def _preprocess_elements(self, elements: list) -> list:
        processed = []
        for el in elements:
            new_el = el.copy()
            if 'content' in new_el:
                new_el['content'] = sanitize_text(str(new_el['content']))
            if new_el.get('type') == 'image':
                img = await self._fetch_and_process_image(new_el)
                if img: new_el['_cached_img'] = img
            processed.append(new_el)
        return processed

    async def _ensure_display_ready(self):
        if not self._is_on:
            async with self._ble_lock:
                try:
                    await self._client.set_state(True)
                    self._is_on = True
                except: return
        async with self._ble_lock:
            if self._current_mode != 0:
                try:
                    await self._client.set_mode(0)
                    self._current_mode = 0
                except: self._current_mode = 0 

    async def _send_frame(self, image: Image.Image):
        image = image.convert('RGB')
        img_byte_arr = BytesIO()
        image.save(img_byte_arr, format='PNG', compress_level=0)
        new_bytes = img_byte_arr.getvalue()
        if self._client.get_last_frame() != new_bytes:
            async with self._ble_lock:
                try: await self._client.send_frame_png(image)
                except Exception as e: _LOGGER.debug(f"Frame send error: {e}")

    def _render_canvas_sync(self, elements: list, background: list) -> Image.Image:
        bg = tuple(self._apply_brightness_to_color(tuple(background)))
        if len(bg) == 3: bg += (255,)
        canvas = Image.new('RGBA', (self._width, self._height), bg)
        
        for el in elements:
            try:
                etype = el.get('type')
                if etype == 'text': self._draw_text_element(canvas, el)
                elif etype == 'pixels': self._draw_pixels_element(canvas, el)
                elif etype in ['icon', 'mdi']: self._draw_mdi_element(canvas, el)
                elif etype == 'image' and '_cached_img' in el:
                    img = self._apply_brightness_to_image(el['_cached_img'])
                    canvas.paste(img, (int(el.get('x',0)), int(el.get('y',0))), img if img.mode == 'RGBA' else None)
            except Exception as e: _LOGGER.debug(f"Render error for {el.get('type')}: {e}")
            
        final = Image.new("RGB", canvas.size, (0, 0, 0))
        final.paste(canvas, (0, 0), mask=canvas)
        return final

    def _apply_brightness_to_image(self, img: Image.Image) -> Image.Image:
        if self._brightness == 255: return img
        scale = self._brightness / 255.0
        img = img.copy()
        pixels = img.load()
        for y in range(img.size[1]):
            for x in range(img.size[0]):
                p = pixels[x, y]
                pixels[x, y] = tuple(int(c * scale) for c in p[:3]) + (p[3:] if len(p) > 3 else ())
        return img

    def _get_char_mask(self, font_name: str, char: str) -> Tuple[Optional[Image.Image], int]:
        key = (font_name, char)
        if key in self._char_mask_cache: return self._char_mask_cache[key]

        mask, advance = None, 0
        if font_name == 'awtrix':
            code = ord(char)
            if 32 <= code <= 126:
                idx = code - 32
                if idx < len(AWTRIX_GLYPHS):
                    bo, w, h, adv, xo, yo = AWTRIX_GLYPHS[idx]
                    advance = adv
                    if w > 0 and h > 0:
                        mask = Image.new('1', (w, h), 0)
                        bits, bit_cnt, cur_idx = 0, 0, bo
                        for yy in range(h):
                            for xx in range(w):
                                if (bit_cnt & 7) == 0:
                                    bits = AWTRIX_BITMAPS[cur_idx] if cur_idx < len(AWTRIX_BITMAPS) else 0
                                    cur_idx += 1
                                bit_cnt += 1
                                if bits & 0x80: mask.putpixel((xx, yy), 1)
                                bits <<= 1
            else: advance = 4
        else:
            data, cw, ch, stride = (FONT_3X5_DATA, 3, 5, 3) if font_name == '3x5' else (FONT_5X7_DATA, 5, 7, 7)
            advance = cw
            code = ord(char)
            if code * stride < len(data):
                mask = Image.new('1', (cw, ch), 0)
                offset = code * stride
                for col in range(cw):
                    byte = data[offset + col]
                    for row in range(ch):
                        if (byte >> row) & 1: mask.putpixel((col, row), 1)

        self._char_mask_cache[key] = (mask, advance or 4)
        return mask, advance or 4

    def _draw_text_element(self, canvas: Image.Image, el: Dict[str, Any]) -> None:
        content, x, y = str(el.get('content', '')), int(el.get('x', 0)), int(el.get('y', 0))
        color = tuple(self._apply_brightness_to_color(tuple(el.get('color', [255, 255, 255]))))
        if len(color) == 3: color += (255,)
        font, spacing = el.get('font', '5x7'), int(el.get('spacing', 1))
        cursor_x = x
        extra = spacing - (1 if font == 'awtrix' else 0)

        for char in content:
            mask, adv = self._get_char_mask(font, char)
            if mask:
                dx, dy = cursor_x, y
                if font == 'awtrix':
                    idx = ord(char) - 32
                    if 0 <= idx < len(AWTRIX_GLYPHS):
                        _, _, _, _, xo, yo = AWTRIX_GLYPHS[idx]
                        dx, dy = dx + xo, dy + 5 + yo
                try: canvas.paste(color, (dx, dy), mask)
                except: pass
            cursor_x += adv + extra

    def _draw_pixels_element(self, canvas: Image.Image, el: Dict[str, Any]) -> None:
        pixels = el.get('pixels', [])
        layer = Image.new('RGBA', (self._width, self._height), (0, 0, 0, 0))
        draw = layer.load()
        for p in pixels:
            if len(p) >= 5:
                clr = self._apply_brightness_to_color((p[2], p[3], p[4]))
                try: draw[p[0], p[1]] = (clr[0], clr[1], clr[2], p[5] if len(p) > 5 else 255)
                except: pass
        canvas.alpha_composite(layer)

    def _draw_mdi_element(self, canvas: Image.Image, el: Dict[str, Any]):
        if not self._mdi_ready: return
        name = str(el.get('name', 'mdi:help'))
        code = self._mdi_map.get(name[4:] if name.startswith("mdi:") else name)
        if not code: return
        size, c = int(el.get('size', 16)), el.get('color', [255, 255, 255])
        color = tuple(self._apply_brightness_to_color(tuple(c))) + (255,)
        font = self._mdi_fonts.get(size)
        if not font:
            try:
                font = ImageFont.truetype(self._font_path, size)
                self._mdi_fonts[size] = font
            except: return
        layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((int(el.get('x', 0)), int(el.get('y', 0))), chr(int(code, 16)), font=font, fill=color)
        canvas.alpha_composite(layer)

    async def _fetch_and_process_image(self, el: Dict[str, Any]) -> Optional[Image.Image]:
        data = None
        if 'path' in el and self._hass.config.is_allowed_path(el['path']):
            try: data = await self._hass.async_add_executor_job(lambda: open(el['path'], "rb").read())
            except: pass
        elif 'url' in el:
            try:
                async with async_get_clientsession(self._hass).get(el['url'], timeout=10) as resp:
                    if resp.status == 200: data = await resp.read()
            except: pass
        if data:
            try:
                img = Image.open(BytesIO(data)).convert("RGBA")
                w, h = el.get('width'), el.get('height')
                if w and h: img = img.resize((int(w), int(h)), Image.Resampling.NEAREST)
                return img
            except: pass
        return None