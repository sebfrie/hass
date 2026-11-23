"""Firmware update entity: the offer disappears on install and comes back if the build did not change.

Run: pip install pytest-homeassistant-custom-component asyncssh aiofiles && pytest
"""
from unittest.mock import MagicMock

import asyncssh
import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openipc import update
from custom_components.openipc.update import OpenIPCFirmwareUpdate, _parse_os_info

OLD = "nightly-20261121-7f3c1e9"
NEW = "nightly-20261123-9e6a052"


class FakeConn:
    def __init__(self, stdout=None):
        self.stdout = stdout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, cmd, **kwargs):
        if self.stdout is None:  # sysupgrade: camera reboots and drops the session
            raise asyncssh.ConnectionLost("reboot")
        return MagicMock(stdout=self.stdout, exit_status=0)


def os_info(build):
    return f'NAME="OpenIPC"\nBUILD_ID="{build}"\nCHIP=ssc338q\nSENSOR=imx415\n'


@pytest.mark.parametrize(("booted", "offered"), [(NEW, False), (OLD, True)])
async def test_install_then_verify(hass, monkeypatch, booted, offered):
    monkeypatch.setattr(update, "REBOOT_GRACE", 0)
    entry = MockConfigEntry(domain="openipc", data={"name": "Cam"})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("openipc", entry.entry_id)}
    )

    entity = OpenIPCFirmwareUpdate(MagicMock(data={"parsed": {}}), entry)
    entity.hass = hass
    entity.entity_id = "update.cam_firmware"
    entity.registry_entry = MagicMock(device_id=device.id)
    entity.async_write_ha_state = MagicMock()
    entity._installed = OLD
    entity._latest = {"tag_name": NEW}
    assert entity.state == "on"

    conns = iter([FakeConn(), FakeConn(os_info(booted))])
    entity._connect = lambda: _ready(next(conns))

    await entity.async_install(NEW, False)
    assert entity.state == "off"  # no offer while the camera reboots

    await hass.async_block_till_done(wait_background_tasks=True)
    assert (entity.state == "on") is offered
    device = dr.async_get(hass).async_get(device.id)
    assert device.sw_version == booted
    assert device.hw_version == "SSC338Q / IMX415"


async def _ready(value):
    return value


def test_parse_os_info_skips_empty_values():
    assert _parse_os_info('BUILD_ID="x"\nCHIP=\nnoise') == {"BUILD_ID": "x"}
