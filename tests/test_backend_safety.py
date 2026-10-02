"""Backend regressions with real HA entity classes, but no LAN or HA instance."""

import asyncio
import json
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
import yaml
from homeassistant.exceptions import HomeAssistantError

import custom_components.miwifi as integration
from custom_components.miwifi import device_tracker, services
from custom_components.miwifi.exceptions import (
    LuciError,
    LuciRequestError,
    LuciWriteUncertainError,
)
from custom_components.miwifi.luci import LuciClient
from custom_components.miwifi.operations import OperationError, OperationManager
from custom_components.miwifi.privacy import redact
from custom_components.miwifi.readback import bandwidth, port_rules
from custom_components.miwifi.light import MiWifiLight
from custom_components.miwifi.switch import MiWifiSwitch, MiWifiQosSwitch
from custom_components.miwifi.select import MiWifiSelect, MiWifiQosModeSelect
from custom_components.miwifi.updater import LuciUpdater
from custom_components.miwifi.update import MiWifiUpdate
from .test_client_identity import MAC, OTHER, IP, clients, reservations


class OperationTests(unittest.IsolatedAsyncioTestCase):
    def setup_operation(self, values, desired=1, command=None):
        manager = OperationManager(delay=0)
        read = AsyncMock(side_effect=values)
        command = command or AsyncMock()
        state = {"value": values[0]}
        transitions = []

        def apply(value):
            state["value"] = value

        def notify():
            transitions.append((state["value"], manager.records["key"].status))

        async def run():
            return await manager.execute(
                "key",
                desired,
                command,
                read,
                apply,
                notify,
                optimistic=lambda: apply(desired),
            )

        return manager, state, transitions, read, command, run

    async def test_optimism_then_confirm(self):
        manager, state, changes, read, command, run = self.setup_operation([0, 1])
        await run()
        self.assertEqual(changes, [(1, "pending"), (1, "confirmed")])
        command.assert_awaited_once()

    async def test_refusal_rolls_back_actual_not_opposite_literal(self):
        manager, state, changes, read, command, run = self.setup_operation(
            [20, 20, 20, 20], 50
        )
        with self.assertRaises(OperationError):
            await run()
        self.assertEqual(state["value"], 20)
        self.assertEqual(changes[-1], (20, "failed"))
        command.assert_awaited_once()

    async def test_lost_write_reply_recovers_by_readback(self):
        command = AsyncMock(side_effect=LuciWriteUncertainError("lost reply"))
        manager, state, changes, read, command, run = self.setup_operation(
            [0, 1], command=command
        )
        self.assertEqual(await run(), 1)
        command.assert_awaited_once()
        self.assertEqual(manager.records["key"].status, "confirmed")

    async def test_unavailable_readback_marks_unknown_and_rolls_back(self):
        manager, state, changes, read, command, run = self.setup_operation(
            [0, LuciError("offline"), LuciError("offline"), LuciError("offline")]
        )
        with self.assertRaises(OperationError):
            await run()
        self.assertEqual(changes[-1], (0, "unknown"))
        command.assert_awaited_once()
        manager.observe("key")
        self.assertEqual(manager.records["key"].status, "confirmed")

    async def test_failed_preflight_never_writes(self):
        manager, state, changes, read, command, run = self.setup_operation(
            [LuciError("offline")]
        )
        with self.assertRaises(OperationError):
            await run()
        command.assert_not_called()

    async def test_idempotent_request_skips_write(self):
        manager, state, changes, read, command, run = self.setup_operation([1])
        await run()
        command.assert_not_called()
        self.assertEqual(changes, [(1, "confirmed")])

    async def test_cancel_restores_and_releases_lock(self):
        started = asyncio.Event()

        async def command():
            started.set()
            await asyncio.Future()

        manager, state, changes, read, cmd, run = self.setup_operation(
            [0], command=command
        )
        task = asyncio.create_task(run())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(changes[-1], (0, "unknown"))
        self.assertFalse(manager.lock.locked())

    async def test_concurrent_requests_and_poll_cannot_clobber_each_other(self):
        manager = OperationManager(delay=0)
        state = {"router": 0, "ui": 0}
        events = []

        async def read():
            events.append(("read", state["router"]))
            await asyncio.sleep(0)
            return state["router"]

        async def change(target):
            async def write():
                events.append(("write", target))
                await asyncio.sleep(0)
                state["router"] = target

            await manager.execute(
                "key",
                target,
                write,
                read,
                lambda value: state.update(ui=value),
                lambda: None,
                optimistic=lambda: state.update(ui=target),
            )

        async def poll():
            async with manager.lock:
                state["ui"] = await read()

        await asyncio.gather(change(1), change(0), poll())
        self.assertEqual(state, {"router": 0, "ui": 0})
        self.assertEqual(
            [item for item in events if item[0] == "write"],
            [("write", 1), ("write", 0)],
        )
        self.assertEqual(manager.records["key"].request_id, 2)

    async def test_readback_deadline_is_bounded(self):
        manager = OperationManager(timeout=0.01, delay=0)
        read = AsyncMock(side_effect=[0])

        async def command():
            await asyncio.Future()

        apply = Mock()
        with self.assertRaises(OperationError):
            await manager.execute("key", 1, command, read, apply, Mock())
        self.assertEqual(manager.records["key"].status, "unknown")
        apply.assert_called_with(0)

    async def test_shutdown_cancels_queued_and_active_commands(self):
        manager = OperationManager(delay=0)
        entered = asyncio.Event()

        async def first_command():
            entered.set()
            await asyncio.Future()

        first = asyncio.create_task(
            manager.execute(
                "first", 1, first_command, AsyncMock(return_value=0), Mock(), Mock()
            )
        )
        await entered.wait()
        second_command = AsyncMock()
        second = asyncio.create_task(
            manager.execute(
                "second", 1, second_command, AsyncMock(return_value=0), Mock(), Mock()
            )
        )
        await asyncio.sleep(0)
        await manager.async_shutdown()
        self.assertTrue(first.cancelled())
        self.assertTrue(second.cancelled())
        second_command.assert_not_called()
        self.assertFalse(manager.lock.locked())


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def client(self, response=None, error=None):
        transport = AsyncMock()
        transport.__aenter__.return_value = transport
        transport.post.side_effect = error
        transport.post.return_value = response
        transport.get.return_value = response
        luci = LuciClient(transport, password="never-log-this")
        luci._token = "never-log-token"
        return luci, transport

    async def test_malformed_post_is_uncertain_not_success(self):
        luci, transport = self.client(error=httpx.RemoteProtocolError("illegal header"))
        with self.assertRaises(LuciWriteUncertainError):
            await luci.set_qos_switch(1)
        transport.post.assert_awaited_once()

    async def test_http_403_with_code_zero_is_not_success(self):
        response = httpx.Response(
            403,
            json={"code": 0},
            request=httpx.Request("POST", "http://router/;stok=secret"),
        )
        luci, transport = self.client(response=response)
        with self.assertRaises(LuciWriteUncertainError) as caught:
            await luci.set_qos_switch(1)
        self.assertIsNone(caught.exception.__cause__)

    async def test_get_validates_http_status(self):
        response = httpx.Response(
            500,
            json={"code": 0},
            request=httpx.Request("GET", "http://router/;stok=secret"),
        )
        luci, transport = self.client(response=response)
        with self.assertRaises(LuciError):
            await luci.status()

    async def test_expired_get_session_requests_reauthentication(self):
        response = httpx.Response(
            403, json={"code": 0}, request=httpx.Request("GET", "http://router")
        )
        luci, _ = self.client(response=response)
        with self.assertRaises(LuciRequestError):
            await luci.status()

    async def test_debug_and_diagnostics_do_not_contain_credentials(self):
        response = httpx.Response(
            200,
            json={"code": 0, "pwd": "wifi-secret", "token": "response-secret"},
            request=httpx.Request("POST", "http://router/;stok=secret"),
        )
        luci, transport = self.client(response=response)
        with self.assertLogs(
            "custom_components.miwifi.luci", level=logging.DEBUG
        ) as logs:
            await luci.post("misystem/test", {"password": "never-log-this"})
            luci._debug(
                "Failure",
                "http://router/;stok=secret",
                ValueError("token=secret"),
                "test?password=secret",
            )
        text = json.dumps(luci.diagnostics) + " ".join(logs.output)
        for secret in (
            "never-log-this",
            "never-log-token",
            "wifi-secret",
            "response-secret",
            "token=secret",
        ):
            self.assertNotIn(secret, text)

    def test_invalid_envelopes_are_rejected(self):
        for response in ([], {}, {"code": -1}, {"code": "0"}, {"code": False}):
            with self.subTest(response=response), self.assertRaises(LuciRequestError):
                LuciClient._validate_response(response)


class EntityTests(unittest.IsolatedAsyncioTestCase):
    def entity(self, cls, key, initial, reads):
        entity = cls.__new__(cls)
        entity.entity_description = SimpleNamespace(key=key)
        entity._updater = SimpleNamespace(
            data={key: initial},
            operations=OperationManager(delay=0),
            read_control=AsyncMock(side_effect=reads),
            schedule_followup_refresh=Mock(),
            luci=SimpleNamespace(
                led=AsyncMock(),
                set_wifi=AsyncMock(),
                set_guest_wifi=AsyncMock(),
                set_qos_switch=AsyncMock(),
                set_qos_mode=AsyncMock(),
            ),
        )
        entity._attr_is_on = initial
        entity._attr_current_option = initial
        entity._attr_options = ["min", "mid", "max"]
        entity.async_write_ha_state = Mock()
        return entity

    async def test_led_failure_rolls_back_and_raises_ha_error(self):
        entity = self.entity(MiWifiLight, "led", False, [False, False, False, False])
        entity._updater.luci.led.side_effect = LuciError("refused")
        with self.assertRaises(HomeAssistantError):
            await entity.async_turn_on()
        self.assertIs(entity._attr_is_on, False)
        entity._updater.luci.led.assert_awaited_once_with(1)
        self.assertEqual(entity.extra_state_attributes["operation_status"], "failed")

    async def test_wifi_failure_does_not_publish_false_success(self):
        entity = self.entity(MiWifiSwitch, "wifi_2_4", False, [False] * 4)
        entity._updater.luci.set_wifi.side_effect = LuciError("refused")
        with self.assertRaises(HomeAssistantError):
            await entity.async_turn_on()
        self.assertIs(entity._attr_is_on, False)
        entity._updater.luci.set_wifi.assert_awaited_once()

    async def test_qos_duplicate_on_does_not_flip_off(self):
        entity = self.entity(MiWifiQosSwitch, "qos_on", True, [1])
        entity._updater.luci.set_qos_switch.side_effect = LuciError("refused")
        await entity.async_turn_on()
        self.assertIs(entity._attr_is_on, True)
        entity._updater.luci.set_qos_switch.assert_not_called()

    async def test_wifi_select_failure_restores_exact_previous_option(self):
        entity = self.entity(
            MiWifiSelect, "wifi_2_4_signal_strength", "min", ["min"] * 4
        )
        entity._updater.luci.set_wifi.side_effect = LuciError("refused")
        with self.assertRaises(HomeAssistantError):
            await entity.async_select_option("max")
        self.assertEqual(entity._attr_current_option, "min")
        entity._updater.luci.set_wifi.assert_awaited_once()

    async def test_qos_select_refusal_restores_not_default(self):
        entity = self.entity(MiWifiQosModeSelect, "qos_mode", 4, [4] * 4)
        with self.assertRaises(HomeAssistantError):
            await entity.async_select_option("video")
        self.assertEqual(entity._attr_current_option, "game")


class TrackerTests(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_device_gets_own_identity_and_platform_is_captured(self):
        for initially_empty in (False, True):
            with self.subTest(initially_empty=initially_empty):
                first = {"mac": MAC, "updater_entry_id": "entry"}
                second = {"mac": OTHER, "updater_entry_id": "entry"}
                updater = SimpleNamespace(
                    devices={} if initially_empty else {MAC: first}
                )
                add = Mock()
                callbacks = []
                with (
                    patch.object(
                        device_tracker, "async_get_updater", return_value=updater
                    ),
                    patch.object(
                        device_tracker,
                        "async_get_current_platform",
                        return_value=SimpleNamespace(entities={}),
                    ) as platform,
                    patch.object(
                        device_tracker,
                        "get_config_value",
                        side_effect=lambda entry, key, default: default,
                    ),
                    patch.object(device_tracker, "MiWifiDeviceTracker") as tracker,
                    patch.object(
                        device_tracker,
                        "async_dispatcher_connect",
                        side_effect=lambda h, s, cb: callbacks.append(cb),
                    ),
                ):
                    await device_tracker.async_setup_entry(
                        Mock(), SimpleNamespace(entry_id="entry"), add
                    )
                    callbacks[0](second)
                    callbacks[0](second)
                    self.assertEqual(tracker.call_args.args[0], f"miwifi-{OTHER}")
                    self.assertEqual(tracker.call_count, 1 if initially_empty else 2)
                    platform.assert_called_once()

    async def test_async_port_scan_does_not_block_event_loop(self):
        tracker = device_tracker.MiWifiDeviceTracker.__new__(
            device_tracker.MiWifiDeviceTracker
        )
        tracker._device = {"ip": IP}
        tracker._attr_available = True
        tracker._is_connected = True
        tracker.coordinator = SimpleNamespace(last_update_success=True)
        tracker._updater = SimpleNamespace(port_scan_slots=asyncio.Semaphore(1))
        writer = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
        started, finish = asyncio.Event(), asyncio.Event()

        async def connection(ip, port):
            started.set()
            await finish.wait()
            return None, writer

        tracker._update_entry = Mock()
        with patch.object(
            device_tracker.asyncio, "open_connection", side_effect=connection
        ):
            task = asyncio.create_task(tracker.check_ports())
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0)  # Reached while connection is still pending.
            self.assertFalse(task.done())
            finish.set()
            await task
        self.assertEqual(tracker._configuration_port, 80)
        writer.close.assert_called_once()

    async def test_offline_client_is_not_port_scanned(self):
        tracker = device_tracker.MiWifiDeviceTracker.__new__(
            device_tracker.MiWifiDeviceTracker
        )
        tracker._device = {"ip": IP}
        tracker._attr_available = True
        tracker._is_connected = False
        tracker.coordinator = SimpleNamespace(last_update_success=True)
        with patch.object(
            device_tracker.asyncio, "open_connection", new_callable=AsyncMock
        ) as connection:
            await tracker.check_ports()
        connection.assert_not_called()


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def updater(self):
        updater = LuciUpdater.__new__(LuciUpdater)
        updater.operations = OperationManager(delay=0)
        updater.data = {}
        updater.devices = {}
        updater._activity_days = 30
        updater._entry_id = "entry"
        updater._store = None
        updater._stopped = False
        updater._background_tasks = set()
        updater.hass = Mock()
        updater.luci = SimpleNamespace(
            macbind_info=AsyncMock(return_value=reservations()),
            device_list=AsyncMock(return_value={"list": []}),
            logout=AsyncMock(),
            led=AsyncMock(),
        )
        return updater

    async def test_led_read_does_not_treat_missing_status_as_off(self):
        updater = self.updater()
        updater.luci.led.return_value = {"code": 0}
        with self.assertRaises(ValueError):
            await updater.read_control("led")

    async def test_cleanup_preview_protects_reservations_online_and_shared_clients(
        self,
    ):
        updater = self.updater()
        online, shared = "02:12:34:56:78:94", "02:12:34:56:78:96"
        updater.devices = {
            mac: {"last_activity": "2000-01-01T00:00:00"}
            for mac in (MAC, OTHER, online, shared)
        }
        updater.luci.device_list.return_value = {"list": [{"mac": online, "online": 1}]}
        registry, entities = Mock(), Mock()
        registry.async_get_device.side_effect = lambda ids, connections: (
            SimpleNamespace(config_entries={"entry", "other"})
            if ("mac", shared) in connections
            else None
        )
        with (
            patch(
                "custom_components.miwifi.updater.dr.async_get", return_value=registry
            ),
            patch(
                "custom_components.miwifi.updater.er.async_get", return_value=entities
            ),
        ):
            result = await updater.async_cleanup_stale_clients()
        self.assertEqual(result["candidates"], [OTHER])
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(updater.devices), 4)
        self.assertEqual(result["removed_clients"], 0)
        registry.async_remove_device.assert_not_called()
        entities.async_remove.assert_not_called()

    async def test_cleanup_read_failure_never_deletes(self):
        updater = self.updater()
        updater.devices = {OTHER: {"last_activity": "2000-01-01T00:00:00"}}
        updater.luci.macbind_info.side_effect = LuciError("offline")
        with self.assertRaises(LuciError):
            await updater.async_cleanup_stale_clients(dry_run=False)
        self.assertIn(OTHER, updater.devices)

    def test_automatic_ageing_retains_old_identity(self):
        updater = self.updater()
        updater.devices = {
            MAC: {"last_activity": "2000-01-01T00:00:00", "mac_bound": True}
        }
        updater._clean_devices()
        self.assertIn(MAC, updater.devices)
        self.assertTrue(updater.devices[MAC]["stale"])

    async def test_unload_cancels_delayed_refresh_and_is_idempotent(self):
        updater = self.updater()
        updater._async_save_devices = AsyncMock()
        updater.new_device_callback = Mock()
        unsubscribe = updater.new_device_callback
        task = asyncio.create_task(asyncio.sleep(3600))
        updater._background_tasks.add(task)
        await updater.async_stop()
        await updater.async_stop()
        self.assertTrue(task.cancelled())
        unsubscribe.assert_called_once()
        updater.luci.logout.assert_awaited_once()

    async def test_install_does_not_fabricate_latest_version(self):
        entity = MiWifiUpdate.__new__(MiWifiUpdate)
        entity.entity_description = SimpleNamespace(key="firmware")
        entity._attr_installed_version = "old"
        entity._attr_latest_version = "new"
        entity._firmware_install = AsyncMock()
        entity.async_write_ha_state = Mock()
        await entity.async_install(None, False)
        self.assertEqual(entity._attr_installed_version, "old")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, cls):
        updater = SimpleNamespace(
            operations=OperationManager(delay=0),
            data={},
            devices={MAC: {"ip": IP, "wan": 1}},
            schedule_followup_refresh=Mock(),
            luci=SimpleNamespace(
                device_list=AsyncMock(return_value=clients()),
                macbind_info=AsyncMock(return_value=reservations()),
                set_mac_filter=AsyncMock(),
                mac_bind=AsyncMock(),
                mac_unbind=AsyncMock(),
                qos_info=AsyncMock(),
                set_qos=AsyncMock(),
                set_band=AsyncMock(),
                port_forward_list=AsyncMock(return_value={"list": []}),
                add_port_forward=AsyncMock(),
                delete_port_forward=AsyncMock(),
                wifi_timer_info=AsyncMock(),
                set_wifi_timer=AsyncMock(),
            ),
        )
        handler = cls(Mock())
        handler.get_updater = Mock(return_value=updater)
        handler._publish_optimistic_update = Mock()
        return handler, updater

    async def test_lost_wan_reply_then_confirmed(self):
        handler, updater = self.service(services.MiWifiSetMacFilterServiceCall)
        updater.luci.device_list.side_effect = [clients(1), clients(0)]
        updater.luci.set_mac_filter.side_effect = LuciWriteUncertainError("lost")
        result = await handler.async_call_service(
            SimpleNamespace(data={"mac": MAC, "wan": 0})
        )
        self.assertTrue(result["verified"])
        updater.luci.set_mac_filter.assert_awaited_once()

    async def test_binding_collision_in_current_lease_prevents_write(self):
        handler, updater = self.service(services.MiWifiMacBindServiceCall)
        updater.luci.macbind_info.return_value = {
            "list": [],
            "devicelist": [{"mac": OTHER, "ip": IP}],
        }
        with self.assertRaises(services.vol.Invalid):
            await handler.async_call_service(
                SimpleNamespace(data={"mac": MAC, "ip": IP, "name": "phone"})
            )
        updater.luci.mac_bind.assert_not_called()

    async def test_lost_binding_reply_confirmed_by_full_reservation(self):
        handler, updater = self.service(services.MiWifiMacBindServiceCall)
        updater.luci.macbind_info.side_effect = [
            reservations(ip="192.168.31.60"),
            reservations(),
        ]
        updater.luci.mac_bind.side_effect = LuciWriteUncertainError("lost")
        result = await handler.async_call_service(
            SimpleNamespace(data={"mac": MAC, "ip": IP, "name": "phone"})
        )
        self.assertTrue(result["verified"])
        self.assertEqual(updater.devices[MAC]["bound_ip"], IP)

    async def test_qos_missing_readback_never_writes(self):
        handler, updater = self.service(services.MiWifiSetQosServiceCall)
        updater.luci.qos_info.return_value = {"list": []}
        with self.assertRaises(services.vol.Invalid):
            await handler.async_call_service(
                SimpleNamespace(data={"mac": MAC, "upload": 10, "download": 20})
            )
        updater.luci.set_qos.assert_not_called()

    async def test_qos_limits_confirm_decimal_values(self):
        handler, updater = self.service(services.MiWifiSetQosServiceCall)
        updater.luci.qos_info.side_effect = [
            {"list": [{"mac": MAC, "qos": {"upmax": 0, "downmax": 0}}]},
            {"list": [{"mac": MAC, "qos": {"upmax": "10.00", "downmax": "20.00"}}]},
        ]
        await handler.async_call_service(
            SimpleNamespace(data={"mac": MAC, "upload": 10, "download": 20})
        )
        self.assertEqual(updater.devices[MAC]["qos_down"], 20)
        updater.luci.set_qos.assert_awaited_once()

    async def test_port_forward_refusal_rolls_back_empty_list(self):
        handler, updater = self.service(services.MiWifiAddPortForwardServiceCall)
        with self.assertRaises(services.vol.Invalid):
            await handler.async_call_service(
                SimpleNamespace(
                    data={
                        "name": "test",
                        "proto": "tcp",
                        "fwd_ip": IP,
                        "fwd_port": 80,
                        "ext_port": 8080,
                    }
                )
            )
        self.assertEqual(updater.data["port_forward"], [])
        updater.luci.add_port_forward.assert_awaited_once()

    async def test_unsupported_schedule_does_not_send_write(self):
        handler, updater = self.service(services.MiWifiSetWifiTimerServiceCall)
        updater.luci.wifi_timer_info.side_effect = LuciRequestError("unsupported")
        with self.assertRaises(services.vol.Invalid):
            await handler.async_call_service(
                SimpleNamespace(
                    data={
                        "start_time": "22:00",
                        "end_time": "07:00",
                        "days": "1,2,3",
                        "enabled": 1,
                    }
                )
            )
        updater.luci.set_wifi_timer.assert_not_called()

    async def test_cleanup_requires_explicit_confirmation(self):
        handler, updater = self.service(services.MiWifiCleanupStaleClientsServiceCall)
        updater.async_cleanup_stale_clients = AsyncMock()
        with self.assertRaises(services.vol.Invalid):
            await handler.async_call_service(SimpleNamespace(data={"dry_run": False}))
        updater.async_cleanup_stale_clients.assert_not_called()

    async def test_remove_entry_after_unload_is_safe(self):
        store = SimpleNamespace(async_remove=AsyncMock())
        with (
            patch.object(integration, "get_store", return_value=store),
            patch.object(integration, "get_config_value", return_value="192.168.31.1"),
        ):
            await integration.async_remove_entry(
                SimpleNamespace(data={}), SimpleNamespace(entry_id="entry")
            )
        store.async_remove.assert_awaited_once()

    def test_service_selectors_accept_single_id_and_legacy_one_item_list(self):
        self.assertEqual(
            services.MiWifiServiceCall.schema({"device_id": "router"})["device_id"],
            ["router"],
        )
        self.assertEqual(
            services.MiWifiServiceCall.schema({"device_id": ["router"]})["device_id"],
            ["router"],
        )
        with self.assertRaises(services.vol.Invalid):
            services.MiWifiServiceCall.schema({"device_id": ["one", "two"]})

    def test_router_registry_mac_normalization_does_not_reject_router(self):
        handler = services.MiWifiServiceCall(Mock())
        updater = SimpleNamespace(
            data={services.ATTR_DEVICE_MAC_ADDRESS: "AA:BB:CC:DD:EE:F0"}
        )
        device = SimpleNamespace(
            connections={("ip_address", "192.168.31.1"), ("mac", "aa:bb:cc:dd:ee:f0")}
        )
        registry = SimpleNamespace(async_get=Mock(return_value=device))
        with (
            patch.object(services.dr, "async_get", return_value=registry),
            patch.object(services, "async_get_updater", return_value=updater),
        ):
            self.assertIs(
                handler.get_updater(SimpleNamespace(data={"device_id": ["router"]})),
                updater,
            )

    def test_router_service_rejects_client_even_with_ip_connection(self):
        handler = services.MiWifiServiceCall(Mock())
        updater = SimpleNamespace(
            data={services.ATTR_DEVICE_MAC_ADDRESS: "AA:BB:CC:DD:EE:F0"}
        )
        device = SimpleNamespace(
            connections={("ip_address", "192.168.31.1"), ("mac", MAC)}
        )
        registry = SimpleNamespace(async_get=Mock(return_value=device))
        with (
            patch.object(services.dr, "async_get", return_value=registry),
            patch.object(services, "async_get_updater", return_value=updater),
            self.assertRaises(services.vol.Invalid),
        ):
            handler.get_updater(SimpleNamespace(data={"device_id": ["client"]}))

    def test_service_descriptions_match_registered_services(self):
        from pathlib import Path

        descriptions = yaml.safe_load(
            (Path(services.__file__).parent / "services.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(descriptions), {name for name, _ in services.SERVICES})
        for name, cls in services.SERVICES:
            fields = descriptions[name]["fields"]
            expected = {str(key) for key in cls.schema.schema}
            self.assertEqual(set(fields), expected, name)

    def test_missing_bandwidth_or_rules_is_not_zero(self):
        for reader in (bandwidth, port_rules):
            with self.assertRaises(ValueError):
                reader({})

    def test_redaction_anonymizes_mac_keys_and_client_values(self):
        result = redact({MAC: {"name": "private-person", "ip": IP, "pwd": "secret"}})
        text = json.dumps(result)
        for value in (MAC, IP, "private-person", "secret"):
            self.assertNotIn(value, text)

    def test_schedule_validation(self):
        for value in ("25:00", "07:70", "7:30", "tomorrow"):
            with self.assertRaises(ValueError):
                services.schedule_time(value)
        for value in ("0", "1,1", "1;2", "8"):
            with self.assertRaises(ValueError):
                services.schedule_days(value)


if __name__ == "__main__":
    unittest.main()
