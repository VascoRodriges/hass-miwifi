"""Portable regression tests: no HA instance or network/hardware access."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import custom_components.miwifi.services as miwifi_services

from custom_components.miwifi.client_identity import (
    binding_attributes,
    binding_snapshot,
    client_mac,
    client_status,
    validate_binding_target,
    wan_authority,
)
from custom_components.miwifi.exceptions import LuciConnectionError
from custom_components.miwifi.operations import OperationManager
from custom_components.miwifi.services import (
    MiWifiClientStatusServiceCall,
    MiWifiMacBindServiceCall,
    MiWifiMacUnbindServiceCall,
    MiWifiSetMacFilterServiceCall,
)

MAC = "02:12:34:56:78:90"
OTHER = "02:12:34:56:78:92"
IP = "192.168.31.51"


def reservations(mac=MAC, ip=IP):
    return {
        "code": 0,
        "list": [{"mac": mac, "ip": ip, "name": "phone"}],
        "devicelist": [],
    }


def clients(wan=1, mac=MAC):
    return {
        "code": 0,
        "list": [
            {"mac": mac, "authority": {"wan": wan}, "online": 1, "ip": [{"ip": IP}]}
        ],
    }


class IdentityTests(unittest.TestCase):
    def test_mac_normalized_and_private_allowed(self):
        self.assertEqual(client_mac(MAC.lower()), MAC)

    def test_bad_mac_rejected(self):
        for value in (
            None,
            "",
            "00:00:00:00:00:00",
            "FF:FF:FF:FF:FF:FF",
            "01:12:34:56:78:90",
            MAC + "," + OTHER,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client_mac(value)

    def test_actual_reserved_ip_not_current_lease(self):
        response = reservations(ip="192.168.31.60")
        response["devicelist"] = [{"mac": MAC, "ip": IP, "tag": 2}]
        attrs = binding_attributes(binding_snapshot(response), MAC, IP)
        self.assertEqual(attrs["bound_ip"], "192.168.31.60")
        self.assertIs(attrs["binding_ip_matches"], False)

    def test_empty_authoritative_list_wins_over_stale_tag(self):
        response = {
            "code": 0,
            "list": [],
            "devicelist": [{"mac": MAC, "ip": IP, "tag": 2}],
        }
        self.assertEqual(binding_snapshot(response), {})

    def test_tag_only_never_invents_reserved_ip(self):
        response = {"code": 0, "devicelist": [{"mac": MAC, "ip": IP, "tag": 2}]}
        self.assertIsNone(binding_snapshot(response)[MAC]["bound_ip"])
        with self.assertRaises(ValueError):
            validate_binding_target(response, MAC, IP)

    def test_failure_is_unknown_not_unbound(self):
        self.assertIsNone(binding_attributes(None, MAC, IP)["mac_bound"])
        self.assertIs(binding_attributes({}, MAC, IP)["mac_bound"], False)

    def test_duplicate_ip_and_conflicting_mac_fail(self):
        for row in ({"mac": OTHER, "ip": IP}, {"mac": MAC, "ip": "192.168.31.60"}):
            response = reservations()
            response["list"].append(row)
            with self.subTest(row=row), self.assertRaises(ValueError):
                binding_snapshot(response)

    def test_collision_with_reservation_or_current_client_fails(self):
        with self.assertRaises(ValueError):
            validate_binding_target(reservations(mac=OTHER), MAC, IP)
        response = reservations(ip="192.168.31.60")
        response["devicelist"] = [{"mac": OTHER, "ip": IP}]
        with self.assertRaises(ValueError):
            validate_binding_target(response, MAC, IP)

    def test_malformed_client_list_fails(self):
        response = reservations()
        response["devicelist"] = None
        with self.assertRaises(ValueError):
            validate_binding_target(response, MAC, IP)

    def test_unknown_or_ambiguous_wan_fails(self):
        for authority in (None, {}, {"wan": None}, {"wan": True}, {"wan": 7}):
            response = clients()
            response["list"][0]["authority"] = authority
            with self.subTest(authority=authority), self.assertRaises(ValueError):
                wan_authority(response, MAC)
        response = clients()
        response["list"] *= 2
        with self.assertRaises(ValueError):
            wan_authority(response, MAC)

    def test_status_is_readback_and_bound_ip_is_separate(self):
        result = client_status(clients(), reservations(ip="192.168.31.60"), MAC)
        self.assertEqual(result["wan"], 1)
        self.assertEqual(result["ip"], IP)
        self.assertEqual(result["bound_ip"], "192.168.31.60")
        self.assertIs(result["verified"], True)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, cls):
        updater = SimpleNamespace(
            luci=SimpleNamespace(
                device_list=AsyncMock(return_value=clients()),
                macbind_info=AsyncMock(return_value=reservations()),
                set_mac_filter=AsyncMock(),
                mac_bind=AsyncMock(),
                mac_unbind=AsyncMock(),
            ),
            devices={MAC: {"ip": IP, "wan": 1}},
            data={},
            ip="192.168.31.1",
            operations=OperationManager(delay=0),
        )
        service = cls(Mock())
        service.get_updater = Mock(return_value=updater)
        service._schedule_router_refresh = Mock()
        service._publish_optimistic_update = Mock()
        return service, updater

    async def test_status_only_reads(self):
        service, updater = self.service(MiWifiClientStatusServiceCall)
        result = await service.async_call_service(SimpleNamespace(data={"mac": MAC}))
        self.assertIs(result["verified"], True)
        updater.luci.set_mac_filter.assert_not_called()
        updater.luci.mac_bind.assert_not_called()
        updater.luci.mac_unbind.assert_not_called()

    async def test_idempotent_wan_does_not_write(self):
        service, updater = self.service(MiWifiSetMacFilterServiceCall)
        result = await service.async_call_service(
            SimpleNamespace(data={"mac": MAC, "wan": 1})
        )
        self.assertIs(result["changed"], False)
        updater.luci.set_mac_filter.assert_not_called()

    async def test_wan_changes_once_then_confirms(self):
        service, updater = self.service(MiWifiSetMacFilterServiceCall)
        updater.luci.device_list.side_effect = [clients(1), clients(0)]
        result = await service.async_call_service(
            SimpleNamespace(data={"mac": MAC, "wan": 0})
        )
        self.assertIs(result["verified"], True)
        updater.luci.set_mac_filter.assert_awaited_once_with(MAC, 0)
        self.assertEqual(updater.devices[MAC]["wan"], 0)

    async def test_stale_readback_never_claims_success_or_retries_write(self):
        service, updater = self.service(MiWifiSetMacFilterServiceCall)
        with patch(
            "custom_components.miwifi.operations.asyncio.sleep", new_callable=AsyncMock
        ):
            with self.assertRaises(miwifi_services.vol.Invalid):
                await service.async_call_service(
                    SimpleNamespace(data={"mac": MAC, "wan": 0})
                )
        updater.luci.set_mac_filter.assert_awaited_once()
        self.assertEqual(updater.devices[MAC]["wan"], 1)

    async def test_unknown_identity_never_writes(self):
        service, updater = self.service(MiWifiSetMacFilterServiceCall)
        updater.luci.device_list.return_value = clients(mac=OTHER)
        with self.assertRaises(miwifi_services.vol.Invalid):
            await service.async_call_service(
                SimpleNamespace(data={"mac": MAC, "wan": 0})
            )
        updater.luci.set_mac_filter.assert_not_called()

    async def test_collision_prevents_bind(self):
        service, updater = self.service(MiWifiMacBindServiceCall)
        updater.luci.macbind_info.return_value = reservations(mac=OTHER)
        with self.assertRaises(miwifi_services.vol.Invalid):
            await service.async_call_service(
                SimpleNamespace(data={"mac": MAC, "ip": IP, "name": "phone"})
            )
        updater.luci.mac_bind.assert_not_called()

    async def test_bind_readback_required_and_optimistic_state_rolled_back(self):
        service, updater = self.service(MiWifiMacBindServiceCall)
        updater.luci.macbind_info.return_value = reservations(ip="192.168.31.60")
        with self.assertRaises(miwifi_services.vol.Invalid):
            await service.async_call_service(
                SimpleNamespace(data={"mac": MAC, "ip": IP, "name": "phone"})
            )
        updater.luci.mac_bind.assert_awaited_once()
        self.assertEqual(updater.devices[MAC]["bound_ip"], "192.168.31.60")
        self.assertEqual(updater.operations.records[f"binding:{MAC}"].status, "failed")

    async def test_bind_timeout_is_error_not_silent_success(self):
        service, updater = self.service(MiWifiMacBindServiceCall)
        updater.luci.macbind_info.return_value = reservations(ip="192.168.31.60")
        updater.luci.mac_bind.side_effect = LuciConnectionError("Connection error")
        with self.assertRaises(miwifi_services.vol.Invalid):
            await service.async_call_service(
                SimpleNamespace(data={"mac": MAC, "ip": IP, "name": "phone"})
            )
        updater.luci.mac_bind.assert_awaited_once()
        self.assertEqual(updater.devices[MAC]["bound_ip"], "192.168.31.60")

    async def test_verified_unbind(self):
        service, updater = self.service(MiWifiMacUnbindServiceCall)
        updater.luci.macbind_info.side_effect = [
            reservations(),
            {"code": 0, "list": []},
        ]
        result = await service.async_call_service(SimpleNamespace(data={"mac": MAC}))
        self.assertIs(result["mac_bound"], False)
        self.assertIs(updater.devices[MAC]["mac_bound"], False)


if __name__ == "__main__":
    unittest.main()
