import asyncio
import struct
import time
from io import BytesIO
from typing import Optional, Dict, Tuple
from bleak import BleakClient
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from PIL import Image
from .const import IDM_CHAR_WRITE

class UmpBleClient:
    def __init__(self, hass: HomeAssistant, mac: str, width: int, height: int) -> None:
        self._hass = hass
        self._mac = mac
        self._width = width
        self._height = height
        self._client: Optional[BleakClient] = None
        self._lock = asyncio.Lock()
        self._last_image_bytes: Optional[bytes] = None
        self._init_default_image()

    def _init_default_image(self):
        try:
            img = Image.new('RGB', (self._width, self._height), color='black')
            img_byte_arr = BytesIO()
            img.save(img_byte_arr, format='PNG')
            self._last_image_bytes = img_byte_arr.getvalue()
        except Exception:
            pass

    def get_last_frame(self) -> bytes | None:
        return self._last_image_bytes

    async def ensure_connected(self) -> None:
        if self._client and self._client.is_connected:
            return
        async with self._lock:
            if self._client and self._client.is_connected:
                return
            device = bluetooth.async_ble_device_from_address(self._hass, self._mac, connectable=True)
            if device is None:
                raise ConnectionError(f"UMP {self._mac} not available")
            try:
                self._client = await establish_connection(
                    BleakClient,
                    device,
                    self._mac,
                    disconnected_callback=self._on_disconnect,
                )
            except Exception as e:
                raise ConnectionError(f"Failed to connect to UMP {self._mac}") from e

    def _on_disconnect(self, client: BleakClient) -> None:
        self._client = None

    async def write_gatt(self, data: bytes, response: bool = False) -> None:
        await self.ensure_connected()
        try:
            await self._client.write_gatt_char(IDM_CHAR_WRITE, data, response=response)
        except Exception:
            if self._client:
                try:
                    await self._client.disconnect()
                except:
                    pass
            self._client = None
            raise

    async def set_state(self, on: bool) -> None:
        val = 1 if on else 0
        cmd = bytearray([0x06, 0x00, 0x04, 0x00, 0x01, 0x00, val])
        await self.write_gatt(cmd)

    async def set_mode(self, mode: int) -> None:
        cmd = bytearray([0x06, 0x00, 0x03, 0x00, 0x01, 0x00, mode])
        await self.write_gatt(cmd)

    async def clear(self) -> None:
        img = Image.new('RGB', (self._width, self._height), color='black')
        await self.send_frame_png(img)

    async def sync_time(self) -> None:
        now = time.localtime()
        year = now.tm_year - 2000
        cmd = bytearray([
            0x0C, 0x00, 
            0x08, 0x00, 
            year, now.tm_mon, now.tm_mday, 
            now.tm_hour, now.tm_min, now.tm_sec, 
            now.tm_wday + 1
        ])
        await self.write_gatt(cmd)

    @staticmethod
    def _create_image_payloads(png_data: bytes) -> bytearray:
        png_chunks = [png_data[i:i + 65535] for i in range(0, len(png_data), 65535)]
        idk = len(png_data) + len(png_chunks) 
        payloads = bytearray()
        for i, chunk in enumerate(png_chunks):
            header = struct.pack('<HHB', idk, 0, 2 if i > 0 else 0)
            png_len = struct.pack('<I', len(png_data)) 
            payload = bytearray(header) + png_len + chunk
            payloads.extend(payload)
        return payloads

    async def send_frame_png(self, img: Image.Image) -> None:
        if img.size != (self._width, self._height):
            img = img.resize((self._width, self._height), Image.Resampling.NEAREST)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='PNG')
        png_data = img_byte_arr.getvalue()
        
        self._last_image_bytes = png_data
        
        payloads = self._create_image_payloads(png_data)
        chunks = [payloads[i:i + 512] for i in range(0, len(payloads), 512)]
        await self.ensure_connected()
        init_data = bytearray([10, 0, 5, 1, 0, 0, 0, 0, 0, 0])
        await self._client.write_gatt_char(IDM_CHAR_WRITE, bytes(init_data), response=True)
        await asyncio.sleep(0.05)
        for chunk in chunks:
            await self._client.write_gatt_char(IDM_CHAR_WRITE, bytes(chunk), response=False) 

    async def send_frame_dict(self, pixels: Dict[Tuple[int, int], Tuple[int, int, int]]) -> None:
        img = Image.new('RGB', (self._width, self._height), color='black')
        for (x, y), color in pixels.items():
            if 0 <= x < self._width and 0 <= y < self._height:
                img.putpixel((x, y), color)
        await self.send_frame_png(img)
