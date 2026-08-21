"""Config flow for AION EMS."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN, NAME, CONF_DEVELOPER_MODE


class AionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """AION EMS config flow."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle user setup."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=NAME, data=user_input)

        schema = vol.Schema({
            vol.Optional(CONF_DEVELOPER_MODE, default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
