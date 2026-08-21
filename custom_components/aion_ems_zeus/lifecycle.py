"""Lifecycle and clean-uninstall support for AION EMS."""

from __future__ import annotations

from contextlib import suppress
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.frontend import async_remove_panel
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    DATA_LAKE_STORAGE_KEY,
    DATA_LAKE_STORAGE_VERSION,
    DOMAIN,
    PANEL_URL_PATH,
    ENERGY_FLOW_PANEL_URL_PATH,
    COMMAND_CENTER_PANEL_URL_PATH,
    REGISTRY_STORAGE_KEY,
    REGISTRY_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class LifecycleManager:
    """Own cleanup of AION EMS runtime and persistent artifacts."""

    FRONTEND_RELATIVE_PATH = Path("www/aion_ems_zeus/device_manager.js")

    # Known storage keys from current and earlier development builds.
    STORAGE_KEYS: tuple[tuple[str, int], ...] = (
        (REGISTRY_STORAGE_KEY, REGISTRY_STORAGE_VERSION),
        (DATA_LAKE_STORAGE_KEY, DATA_LAKE_STORAGE_VERSION),
        (f"{DOMAIN}.history", 1),
        (f"{DOMAIN}.settings", 1),
        (f"{DOMAIN}.cache", 1),
        (f"{DOMAIN}.knowledge", 1),
        (f"{DOMAIN}.backups", 1),
    )

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def remove_panel(self) -> bool:
        """Remove the integration-owned sidebar panel."""
        removed = False
        for panel_path in (PANEL_URL_PATH, ENERGY_FLOW_PANEL_URL_PATH, COMMAND_CENTER_PANEL_URL_PATH):
            try:
                async_remove_panel(self.hass, panel_path)
                removed = True
            except (KeyError, ValueError):
                continue
            except Exception:
                _LOGGER.exception("Unexpected error removing AION EMS panel %s", panel_path)
        return removed

    async def async_remove_entity_registry_entries(
        self, entry_id: str
    ) -> list[str]:
        """Remove entity-registry records owned by this config entry."""
        registry = er.async_get(self.hass)
        removed: list[str] = []

        for entity in list(registry.entities.values()):
            if (
                entity.config_entry_id == entry_id
                or entity.platform == DOMAIN
            ):
                registry.async_remove(entity.entity_id)
                removed.append(entity.entity_id)

        return removed

    async def async_remove_device_registry_entries(
        self, entry_id: str
    ) -> list[str]:
        """Remove device-registry records owned by this config entry."""
        registry = dr.async_get(self.hass)
        removed: list[str] = []

        for device in list(registry.devices.values()):
            if entry_id in device.config_entries:
                registry.async_remove_device(device.id)
                removed.append(device.id)

        return removed

    async def async_remove_storage(self) -> list[str]:
        """Remove integration-owned Home Assistant storage files."""
        removed: list[str] = []

        for key, version in self.STORAGE_KEYS:
            store: Store[dict[str, Any]] = Store(self.hass, version, key)
            try:
                await store.async_remove()
            except FileNotFoundError:
                continue
            except Exception:
                _LOGGER.exception("Unable to remove AION EMS storage key %s", key)
                continue
            removed.append(key)

        return removed

    async def async_install_frontend_file(self) -> dict[str, Any]:
        """Install or update the bundled Device Manager frontend automatically."""
        source = Path(__file__).parent / "frontend" / "device_manager.js"
        target = Path(self.hass.config.path(str(self.FRONTEND_RELATIVE_PATH)))

        def _copy() -> dict[str, Any]:
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes()
            changed = not target.exists() or target.read_bytes() != content
            if changed:
                target.write_bytes(content)
            return {"installed": target.exists(), "changed": changed, "path": str(target)}

        try:
            return await self.hass.async_add_executor_job(_copy)
        except Exception as err:
            _LOGGER.exception("Unable to install AION EMS frontend file %s", target)
            return {"installed": False, "changed": False, "path": str(target), "error": str(err)}

    async def async_remove_frontend_file(self) -> bool:
        """Remove the frontend file installed by the AION EMS package."""
        path = Path(self.hass.config.path(str(self.FRONTEND_RELATIVE_PATH)))

        try:
            await self.hass.async_add_executor_job(path.unlink, True)
        except TypeError:
            # Compatibility fallback for Python/pathlib variants without
            # the missing_ok positional form.
            def _unlink() -> None:
                with suppress(FileNotFoundError):
                    path.unlink()

            await self.hass.async_add_executor_job(_unlink)
        except Exception:
            _LOGGER.exception("Unable to remove AION EMS frontend file %s", path)
            return False

        # Remove empty integration frontend directory, but never /config/www.
        def _remove_empty_parent() -> None:
            with suppress(OSError):
                path.parent.rmdir()

        await self.hass.async_add_executor_job(_remove_empty_parent)
        return not path.exists()

    async def async_full_cleanup(self, entry_id: str) -> dict[str, Any]:
        """Remove all artifacts owned by an uninstalled AION EMS entry."""
        panel_removed = self.remove_panel()
        entities = await self.async_remove_entity_registry_entries(entry_id)
        devices = await self.async_remove_device_registry_entries(entry_id)
        storage = await self.async_remove_storage()
        frontend_removed = await self.async_remove_frontend_file()

        report = {
            "status": "completed",
            "panel_removed": panel_removed,
            "entity_registry_removed": len(entities),
            "device_registry_removed": len(devices),
            "storage_keys_removed": storage,
            "frontend_removed": frontend_removed,
            "entities": entities,
            "devices": devices,
        }
        _LOGGER.info("AION EMS clean uninstall completed: %s", report)
        return report
