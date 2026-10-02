# MiWiFi for Home Assistant

![hacs](https://img.shields.io/badge/HACS-Custom-orange.svg)
![version](https://img.shields.io/github/v/release/VascoRodriges/hass-miwifi)

This repository is a fork of the original [dmamontov/hass-miwifi](https://github.com/dmamontov/hass-miwifi) project created by Dmitry Mamontov.

The goal of this fork is to keep the MiWiFi integration usable on recent Home Assistant releases while preserving support for Xiaomi and Redmi routers running original MiWiFi firmware, or original firmware with minimal patches.

In addition to Home Assistant compatibility work, this fork includes service improvements, extended `device_tracker` attributes, and optimistic UI updates for selected router actions.

## Fork status

- Upstream project: [dmamontov/hass-miwifi](https://github.com/dmamontov/hass-miwifi)
- Original author: Dmitry Mamontov
- Integration domain: `miwifi`
- Current manifest version in this repository: `4.0.2`
- Device communication model: local polling through the MiWiFi LuCI API

## Important notes

- This fork is intended for routers with original MiWiFi firmware, or original firmware patched without changing the LuCI API behavior.
- Routers running heavily modified or third-party firmware may expose different endpoints or payloads, so some features may be unavailable.
- Portable regression tests cover client identity and backend reconciliation. The legacy upstream pytest suite is not part of this fork's current CI coverage. Real hardware validation was performed only on **Xiaomi Router AX6000 (RA72)**; failure paths and destructive controls are tested with mocks, not by disrupting a live network.
- Other Xiaomi and Redmi models may still work, especially if they were supported by the upstream project, but in this fork they should be treated as unverified until tested.

## What this fork adds

- Adaptation for recent Home Assistant core APIs and platform behavior.
- An options checkbox to preview stale clients without deleting them. Explicit cleanup requires separate confirmation.
- Abstraction layer for LuCI API calls through `api_map.py`, where endpoints, request methods, and parameter mappings are centralized.
- Extended service set for router management and diagnostics.
- Additional `device_tracker` attributes for router-side client state.
- Optimistic updates followed by authoritative read-back, exact rollback and explicit unknown-state reporting.

## Supported Home Assistant features

The integration exposes the following platform types:

- `binary_sensor`
- `sensor`
- `light`
- `button`
- `switch`
- `select`
- `device_tracker`
- `update`

## Installation

### Via HACS (Recommended)
Since this repository is currently a custom fork, you need to add it to HACS manually:
1. Open HACS in your Home Assistant.
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Add the URL of this repository: `https://github.com/VascoRodriges/hass-miwifi`
4. Select **Integration** as the category and click **Add**.
5. Find "MiWiFi" in HACS, click **Download**, and restart Home Assistant.

### Manual Installation
1. Download the latest release from this repository.
2. Copy the `custom_components/miwifi` folder to your Home Assistant `config/custom_components` directory.
3. Restart Home Assistant.

## Configuration

Add the integration from Home Assistant UI:

1. Go to `Settings -> Devices & Services`.
2. Add the `MiWiFi` integration.
3. Enter the router IP address and admin password.
4. Choose the password encryption algorithm if needed.
5. Optionally tune device tracking and polling options.

Available options in this fork include:

- Track client devices
- Minimum stay-online timeout for `device_tracker`
- Scan interval
- Request timeout
- Stale-client age threshold (identities are retained until explicit cleanup)
- Cleanup preview checkbox (does not delete clients during options update)

The Home Assistant UI configuration flow already existed upstream. The cleanup checkbox now previews candidates; removal is a separately confirmed service action.

## LuCI API abstraction

This fork introduces an abstraction layer for LuCI API access in `api_map.py`.

Instead of hardcoding endpoint names and request parameter mappings throughout the request logic, the integration now keeps that information in a single place. When a router firmware update changes an endpoint name, request method, or parameter format, it is usually enough to update `api_map.py` instead of editing multiple request implementations.

This makes the integration easier to maintain and reduces the risk of breaking request handling while adapting to MiWiFi API changes.

## Device tracker enhancements

Compared with the baseline upstream behavior, this fork exposes richer client state in `device_tracker` entities.

Useful attributes include:

- `mac`
- `ip`
- `online`
- `connection`
- `router_mac`
- `signal`
- `down_speed`
- `up_speed`
- `last_activity`
- `wan`
- `mac_bound`
- `qos_down`
- `qos_up`

These attributes make it easier to build automations around client connectivity, WAN access state, MAC binding, and QoS limits.

## Optimistic UI behavior

After a serialized preflight read, this fork publishes the requested value before waiting for the write and confirmation. Controls do not wait for the normal polling cycle. Requests queued behind another operation or poll wait their turn; a cached desired value is never treated as confirmation.

Optimistic updates are implemented for:

- Client WAN block or unblock through `set_mac_filter`
- MAC bind and unbind operations
- Port forwarding add and delete operations
- QoS switch state
- QoS mode select state
- Router LED and WiFi/guest switches, WiFi channel and transmit power
- Per-client QoS limits and global bandwidth

Entity attributes include `operation_status`, `operation_id`, and `pending`.
States are `pending`, `confirmed`, `failed` (fresh actual value differs), and
`unknown` (read-back unavailable). Failure restores the actual state, or the last
confirmed state marked unknown. Unknown router controls are unavailable until a
fresh authoritative poll recovers them. Writes execute at most once; only reads
are retried. Polling and changes share a per-router lock to avoid stale replies
overwriting optimistic state. API transport/protocol failures never fabricate success.

## Services

The integration currently registers the following services:

| Service | Purpose |
|---|---|
| `calc_passwd` | Calculate the default MiWiFi password hash for supported routers |
| `request` | Send an allowlisted read-only LuCI request and publish the response as an event; changes must use dedicated controls |
| `get_client_status` | Read one exact client's authoritative current state |
| `get_capabilities` | Probe read support without changing settings |
| `set_mac_filter` | Block or unblock WAN access for a client MAC address |
| `macbind_info` | Fetch MAC binding information from the router |
| `mac_bind` | Bind an IP address to a MAC address |
| `mac_unbind` | Remove an existing MAC binding |
| `cleanup_stale_clients` | Preview stale clients; removal requires `dry_run: false` and `confirm: true` |
| `port_forward_list` | Read port forwarding rules |
| `add_port_forward` | Add a port forwarding rule |
| `delete_port_forward` | Delete a port forwarding rule |
| `set_band` | Set global WAN bandwidth limits |
| `qos_info` | Read QoS information from the router |
| `set_qos` | Set per-device QoS limits |
| `wifi_timer_info` | Read global WiFi schedule information |
| `set_wifi_timer` | Configure the global WiFi schedule |
| `set_mac_time` | Configure per-device parental control schedule |
| `add_mesh_node` | Add a mesh node by IP address |
| `set_wifi_macfilter` | Configure router-level WiFi MAC filter mode |

### Firmware-dependent / experimental actions

`set_wifi_timer`, `set_mac_time`, `add_mesh_node`, and `set_wifi_macfilter`
currently return **acknowledgement only** (`acknowledged: true`, `verified: false`).
This does not prove a schedule, pairing, or filter was applied. A lost response
raises an error without retrying the write. WiFi scheduling probes its read
endpoint before writing; unsupported firmware fails without submitting a schedule.
`get_capabilities` reports read-probe results, not guaranteed write support.
Do not expose these experimental actions to Assist as verified control tools.

Select the router device, not a tracked client. Services accept a single device
ID; existing one-element `device_id` lists remain compatible. Cleanup protects
online clients, DHCP reservations, and devices belonging to other integrations.
There is no automatic registry deletion on ageing or options save.

## Events

The integration fires the `miwifi_luci` event for data-returning or raw-request services.

This is useful for automations that need access to the raw router response, for example:

- `request`
- `macbind_info`
- `port_forward_list`
- `qos_info`
- `wifi_timer_info`

The event payload includes the target device id, request URI, request body, and router response.

## Router compatibility

The upstream project documents a broader set of Xiaomi and Redmi routers that were reported or tested by the community:

- [Upstream supported routers page](https://github.com/dmamontov/hass-miwifi/wiki/Supported-routers)

For this fork, the current real-device validation status is:

- Verified by the fork maintainer: `Xiaomi Router AX6000 (RA72)`
- Other routers: not yet re-verified in this fork

If your router is based on original MiWiFi firmware and exposes the same LuCI endpoints used by this integration, it may work, but it should be treated as experimental until confirmed.

## LuCI API coverage

Core integration behavior depends on these endpoint groups being available on the router.

Required endpoints:

- `xqsystem/login`
- `xqsystem/init_info`
- `misystem/status`
- `xqnetwork/mode`

Common additional endpoints used by entities and services:

- `misystem/topo_graph`
- `xqsystem/check_rom_update`
- `xqnetwork/wan_info`
- `xqsystem/vpn_status`
- `misystem/led`
- `xqnetwork/wifi_detail_all`
- `xqnetwork/wifi_diag_detail_all`
- `xqnetwork/avaliable_channels`
- `xqnetwork/wifi_connect_devices`
- `misystem/devicelist`
- `xqnetwork/wifiap_signal`
- `misystem/newstatus`
- `xqnetwork/set_wifi`
- `xqnetwork/set_wifi_without_restart`
- `xqsystem/set_mac_filter`
- `xqnetwork/macbind_info`
- `xqnetwork/mac_bind`
- `xqnetwork/mac_unbind`
- `xqnetwork/portforward`
- `xqnetwork/add_redirect`
- `xqnetwork/delete_redirect`
- `misystem/qos_info`
- `misystem/set_qos`
- `xqnetwork/wifi_timer`
- `xqnetwork/set_wifi_timer`
- `misystem/set_mac_time`
- `xqnetwork/set_wifi_macfilter`

Availability depends on router model, region, operating mode, and firmware branch.

### Verified client identity and service responses (4.0.1)

Client trackers now distinguish `ip` (current/last observed address) from
`bound_ip`/`bound_name` (DHCP reservation), with `binding_source` and
`binding_ip_matches`. Failed reads produce unknown binding state, not "unbound".
The explicit reservation list is authoritative; old firmware tag-only fallback
does not invent a reserved address from a current lease.

`get_client_status` reads one exact MAC's WAN permission, connection and addresses.
`set_mac_filter`, `mac_bind`, `mac_unbind` and data-returning services support an
optional HA response. Existing read events remain compatible. WAN and DHCP writes
must be confirmed by router readback; invalid identity, occupied address, hidden
unconfirmed state is an error, not optimistic success. Lost responses can recover
only when fresh read-back proves the requested state. An already-correct
WAN permission is not written again. DHCP changes also check unrelated reservations.

These services are primitives, not an automatic Assist exposure policy. Protect
infrastructure and use a closed allowlist in your own scripts. WAN permission does
not prove internet connectivity and does not block cellular/alternate-gateway access.

Portable tests (no hardware/network):

```sh
python -m unittest tests.test_client_identity tests.test_backend_safety
```

### Backend safety changes (4.0.2)

- Correct dynamic client IDs, captured platform context, and duplicate suppression.
- Nonblocking, bounded client port discovery; offline clients skipped; tasks cancelled on unload.
- Serialized writes/polls with read-back and rollback for regular controls, WAN,
  DHCP, port forwarding and QoS. Existing requests remain single-shot.
- Firmware installation reports the version actually read from the router, never an assumed latest version.
- Session tokens, password/hash payloads and raw exception URLs omitted from logs;
  client identities and credential-bearing response fields redacted in diagnostics.
- Safe entry deletion after unload, coalesced/cancelled refresh tasks, refreshed
  service handlers and complete service field descriptions.
- No household-specific IPs, MACs, names, or voice allowlists in the public integration.

Restart Home Assistant Core after installing Python changes. Reloading only the
config entry does not reliably reload already imported integration modules.

## Credits and upstream

- Original integration architecture and router support matrix: Dmitry Mamontov
- Upstream repository: [dmamontov/hass-miwifi](https://github.com/dmamontov/hass-miwifi)
- This repository: fork focused on newer Home Assistant compatibility and fork-specific feature improvements
