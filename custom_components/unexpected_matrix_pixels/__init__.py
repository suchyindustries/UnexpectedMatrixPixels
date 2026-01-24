from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from .const import DOMAIN, CONF_MAC_ADDRESS, CONF_WIDTH, CONF_HEIGHT, DEFAULT_WIDTH, DEFAULT_HEIGHT
from .ble_client import UmpBleClient

# Added Platform.CAMERA here
PLATFORMS = [Platform.LIGHT, Platform.CAMERA]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    mac = entry.data[CONF_MAC_ADDRESS]
    width = entry.data.get(CONF_WIDTH, DEFAULT_WIDTH)
    height = entry.data.get(CONF_HEIGHT, DEFAULT_HEIGHT)
    client = UmpBleClient(hass, mac, width, height)
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client, 
        "data": entry.data,
        "width": width,
        "height": height
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
