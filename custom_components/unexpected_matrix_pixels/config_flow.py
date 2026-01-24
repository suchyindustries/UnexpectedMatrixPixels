from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from .const import DOMAIN, CONF_MAC_ADDRESS, CONF_WIDTH, CONF_HEIGHT, DEFAULT_WIDTH, DEFAULT_HEIGHT

class UMPConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> FlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        return await self.async_step_user({CONF_MAC_ADDRESS: discovery_info.address})

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_MAC_ADDRESS): str,
                    vol.Optional(CONF_WIDTH, default=DEFAULT_WIDTH): int,
                    vol.Optional(CONF_HEIGHT, default=DEFAULT_HEIGHT): int,
                }),
                errors=errors,
            )
        
        mac = user_input[CONF_MAC_ADDRESS].upper()
        width = user_input.get(CONF_WIDTH, DEFAULT_WIDTH)
        height = user_input.get(CONF_HEIGHT, DEFAULT_HEIGHT)
        
        mac_clean = mac.replace(":", "")
        short_id = mac_clean[-6:]
        title = f"display.{short_id}"

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()
        
        return self.async_create_entry(
            title=title,
            data={
                CONF_MAC_ADDRESS: mac,
                CONF_WIDTH: width,
                CONF_HEIGHT: height
            }
        )
