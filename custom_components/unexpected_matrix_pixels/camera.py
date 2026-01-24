from __future__ import annotations
import logging
from typing import Optional
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, CONF_MAC_ADDRESS
from .ble_client import UmpBleClient # Fixed import name

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    mac = entry.data[CONF_MAC_ADDRESS]
    client_data = hass.data[DOMAIN].get(entry.entry_id)
    if not client_data or "client" not in client_data: return
    client = client_data["client"]
    async_add_entities([IDMDisplayCamera(client, mac, entry.title)])

class IDMDisplayCamera(Camera):

    def __init__(self, client: UmpBleClient, mac: str, entry_title: str) -> None:
        super().__init__()
        self._client = client
        self._mac = mac
        self._attr_name = f"{entry_title} Live View"
        self._attr_unique_id = f"{mac}_live_view"
        
    async def async_camera_image(self, width: Optional[int] = None, height: Optional[int] = None) -> Optional[bytes]:
        return self._client.get_last_frame()
