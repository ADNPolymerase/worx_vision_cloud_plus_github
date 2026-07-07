"""Config flow for Worx Vision Cloud Plus."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback

from pyworxcloud import WorxCloud
from pyworxcloud.exceptions import AuthorizationError, TooManyRequestsError

from .const import (
    CLOUDS,
    CONF_CLOUD,
    CONF_EXPOSE_RAW,
    CONF_VERIFY_SSL,
    DEFAULT_CLOUD,
    DEFAULT_EXPOSE_RAW,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class CannotConnect(Exception):
    """Could not connect."""


class RateLimited(Exception):
    """Cloud returned rate limit."""


async def _validate_input(data: dict[str, Any]) -> None:
    """Validate credentials by authenticating and opening the cloud connection once."""
    cloud = WorxCloud(
        username=data[CONF_EMAIL],
        password=data[CONF_PASSWORD],
        cloud=data.get(CONF_CLOUD, DEFAULT_CLOUD),
        verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        command_timeout=20.0,
        deduplicate_inflight_commands=True,
    )
    try:
        await cloud.authenticate()
        await cloud.connect()
    except AuthorizationError as err:
        raise
    except TooManyRequestsError as err:
        raise RateLimited from err
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Validation failed", exc_info=True)
        raise CannotConnect from err
    finally:
        try:
            await cloud.disconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Validation disconnect failed", exc_info=True)


class WorxVisionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Worx Vision Cloud Plus."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            normalized_email = user_input[CONF_EMAIL].strip().lower()
            user_input[CONF_EMAIL] = normalized_email
            user_input[CONF_CLOUD] = user_input.get(CONF_CLOUD, DEFAULT_CLOUD).lower()

            try:
                await _validate_input(user_input)
            except AuthorizationError:
                errors["base"] = "invalid_auth"
            except RateLimited:
                errors["base"] = "rate_limited"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_CLOUD]}_{normalized_email}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Worx Landroid Vision ({normalized_email})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(user_input),
            errors=errors,
        )

    @callback
    def _schema(self, user_input: dict[str, Any] | None = None) -> vol.Schema:
        """Return user form schema."""
        defaults = user_input or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_EMAIL,
                    default=defaults.get(CONF_EMAIL, ""),
                ): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(
                    CONF_CLOUD,
                    default=defaults.get(CONF_CLOUD, DEFAULT_CLOUD),
                ): vol.In(CLOUDS),
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): bool,
                vol.Optional(
                    CONF_EXPOSE_RAW,
                    default=defaults.get(CONF_EXPOSE_RAW, DEFAULT_EXPOSE_RAW),
                ): bool,
            }
        )
