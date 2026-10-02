# MiWiFi for Home Assistant

![hacs](https://img.shields.io/badge/HACS-Custom-orange.svg)
![release](https://img.shields.io/github/v/release/VascoRodriges/hass-miwifi?label=published%20release)
![main](https://img.shields.io/badge/main-4.0.2-blue)

[Русская инструкция](README.ru.md) · [Installation](#installation) · [YAML examples](#yaml-examples) · [Limitations](#important-notes)

This repository is a fork of the original [dmamontov/hass-miwifi](https://github.com/dmamontov/hass-miwifi) project created by Dmitry Mamontov.

The goal of this fork is to keep the MiWiFi integration usable on recent Home Assistant releases while preserving support for Xiaomi and Redmi routers running original MiWiFi firmware, or original firmware with minimal patches.

Track network clients and manage supported router controls locally from Home Assistant.
This fork adds explicit client identity, DHCP reservation information, read-back
verification and optimistic state with rollback, so a requested value is not
mistaken for an applied router setting. No cloud account, LLM or external proxy is
required for router communication.

## Fork status

- Upstream project: [dmamontov/hass-miwifi](https://github.com/dmamontov/hass-miwifi)
- Original author: Dmitry Mamontov
- Integration domain: `miwifi`
- Current manifest version in this repository: `4.0.2`
- HACS minimum Home Assistant version: `2026.9.4`
- Published releases can lag behind `main`; check the downloaded manifest version
- Device communication model: local polling through the MiWiFi LuCI API

## Important notes

- This fork is intended for routers with original MiWiFi firmware, or original firmware patched without changing the LuCI API behavior.
- Routers running heavily modified or third-party firmware may expose different endpoints or payloads, so some features may be unavailable.
- Portable regression tests cover client identity and backend reconciliation. The legacy upstream pytest suite is not part of this fork's current CI coverage. Real hardware validation was performed only on **Xiaomi Router AX6000 (RA72)**; failure paths and destructive controls are tested with mocks, not by disrupting a live network.
- Other Xiaomi and Redmi models may still work, especially if they were supported by the upstream project, but in this fork they should be treated as unverified until tested.
- This is a custom integration, not a Home Assistant Core integration or a certified Quality Scale tier.
- It provides entities and service actions, not a bundled Lovelace dashboard, client grouping card, natural-language agent, or automatic Assist exposure policy. Those are separate user configurations.
- Blocking WAN access affects this router's permission only: it does not block cellular data, another gateway, or a changed/private MAC address.

## What this fork adds

- Compatibility work and live read-only validation on Home Assistant `2026.9.4`.
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

**Version check:** at the time of this documentation update, the published release
is `v4.0.0`, while the backend described here is `4.0.2` in `main`. HACS may download
the older release. Do not assume adding this repository installs the latest branch
changes. If the desired branch/version is not offered by HACS, use the manual
installation below. No new release is implied by these instructions.

Do not install this fork alongside upstream under the same `miwifi` domain.
Back up your existing component and HA configuration before replacing it.

### Manual Installation

1. Download the desired release, or [the `main` archive](https://github.com/VascoRodriges/hass-miwifi/archive/refs/heads/main.zip) for the unreleased backend described here.
2. Copy the `custom_components/miwifi` folder to your Home Assistant `config/custom_components` directory.
3. Restart Home Assistant.

Verify `config/custom_components/miwifi/manifest.json` reports the expected version.
Upgrading Python files requires a **Home Assistant Core restart**; reloading only
the integration entry is insufficient. No router reboot is needed for installation.

## Configuration

Add the integration from Home Assistant UI:

1. Go to `Settings -> Devices & Services`.
2. Add the `MiWiFi` integration.
3. Enter the router's LAN IP address and its **local router administrator password**, not your WiFi or Xiaomi account password.
4. Leave the default `sha1` encryption unless your router requires `sha256`.
5. Optionally tune device tracking and polling options.

Available options in this fork include:

- Track client devices
- Minimum stay-online timeout for `device_tracker`
- Scan interval
- Request timeout
- Stale-client age threshold (identities are retained until explicit cleanup)
- Cleanup preview checkbox (does not delete clients during options update)

The Home Assistant UI configuration flow already existed upstream. The cleanup checkbox now previews candidates; removal is a separately confirmed service action.

Defaults are client tracking enabled, a 30-second poll interval, a 20-second request
timeout, zero extra stay-online delay, and a 30-day stale threshold. Start with
these defaults; shortening polling aggressively increases router load. A stable
router IP and LAN access to its administration API are prerequisites.

Some configuration entities (guest WiFi, channels, transmit power, diagnostic
sensors) are disabled by default or omitted when not detected. Enable supported
entities in HA's entity settings if needed; do not infer capability from the service
name alone. Start with `get_capabilities` and read-only checks before control actions.

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
- `bound_ip`, `bound_name`, `binding_source`, `binding_ip_matches`
- `qos_down`
- `qos_up`
- `operation_status`, `operation_id`, `pending` (when an operation is recorded)

These attributes make it easier to build automations around client connectivity, WAN access state, MAC binding, and QoS limits.

`ip` is the observed lease; `bound_ip` is the reservation. Changing a reservation
does not renew the client lease immediately. A tracker `online` attribute is a
connection-duration value, not a boolean; `get_client_status` returns a separate
boolean/unknown `online` value. Tracking is polling-based, not a real-time packet
monitor. Devices using private/randomized MACs may appear as new clients.

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

Controlled router entities and affected client trackers report `operation_status`,
`operation_id`, and `pending`; a client tracker reports the latest recorded client operation.
These fields are not a persistent job queue or an operation history.
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
| `calc_passwd` | Derive a firmware-default router password on supported routers; sensitive output |
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

`set_qos` requires a readable matching client row in `qos_info`; a missing row is
not treated as an unlimited setting. When QoS is disabled or the firmware omits
client limits, the action fails before writing. `set_band` uses global Mbps fields;
`set_qos` uses the per-client KB/s fields exposed by this integration. Keep them
distinct and verify your firmware's returned values before relying on limits.

## YAML examples

Replace `ROUTER_DEVICE_ID` with the **router's HA device ID**, not its entity ID or IP.
The MAC below is synthetic; replace it with the selected client's stable MAC.
These examples are HA script sequences. Read actions support `response_variable`;
it is not supported by every control action.

### Read capabilities and one client's status (no setting changes)

```yaml
sequence:
  - action: miwifi.get_capabilities
    data:
      device_id: ROUTER_DEVICE_ID
    response_variable: router_capabilities
  - action: miwifi.get_client_status
    data:
      device_id: ROUTER_DEVICE_ID
      mac: "02:12:34:56:78:90"
    response_variable: client_status
```

### Block or restore WAN access (changes router permission)

Use only after verifying the exact client identity and excluding infrastructure.
`wan: 0` blocks; `wan: 1` restores access. Success means the router's WAN authority
was read back, not that internet reachability was tested.

```yaml
sequence:
  - action: miwifi.set_mac_filter
    data:
      device_id: ROUTER_DEVICE_ID
      mac: "02:12:34:56:78:90"
      wan: 0
    response_variable: result
```

### Preview stale-client cleanup (does not delete anything)

```yaml
sequence:
  - action: miwifi.cleanup_stale_clients
    data:
      device_id: ROUTER_DEVICE_ID
      days: 30
      dry_run: true
    response_variable: preview
```

Review `preview.candidates` first. Actual deletion requires a separate call with
`dry_run: false` **and** `confirm: true`. It removes eligible integration/HA registry
records, not clients or DHCP reservations from the router. Back up HA first.

## Troubleshooting and safe voice integration

- **Login fails:** check LAN reachability, the local admin password, and the router's
  required hash algorithm. A successful ping alone does not prove the LuCI API works.
- **Action fails or state is unknown:** read the router state again. Do not loop a
  write on a timeout: the router may have applied it even when its reply was lost.
- **Schedule unavailable:** use `get_capabilities`; endpoint availability depends on
  firmware. On the validated AX6000 RA72 setup the WiFi schedule read probe fails.
- **Device appears twice or changes identity:** check private/randomized MAC settings;
  a DHCP reservation identifies a MAC, not a person's name or a phone model.
- **HA reports a deprecated device-registry API:** HA 2026.9.4 can warn about
  `async_get_device`; migration is needed before its announced removal in 2027.8.
  This version does not claim compatibility with that future release.

For Assist, expose narrow, separately configured scripts with an explicit client
allowlist. Resolve friendly names to confirmed MACs, protect the router, HA host
and other infrastructure, and require confirmation for disruptive operations.
Do not expose raw API requests, router-wide filters, firmware updates or cleanup
as unrestricted LLM tools. No household-specific names, ranges or allowlists are
shipped in this integration.

## Events

The integration fires the `miwifi_luci` event for data-returning or raw-request services.

This is useful for automations that need access to the raw router response, for example:

- `request`
- `macbind_info`
- `port_forward_list`
- `qos_info`
- `wifi_timer_info`

The event payload includes the target device id, request URI, request body, and router response.
Raw service responses/events may contain client names, IPs, MACs or other private
router data. Diagnostic-download redaction does not sanitize these runtime events;
do not publish them unreviewed in issue reports or logs.

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
- `misystem/qos_switch`
- `misystem/qos_mode`
- `misystem/set_band`
- `misystem/qos_limits`
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
must be confirmed by router readback; invalid identity, an occupied address or
unconfirmed state is an error, not optimistic success. Lost responses can recover
only when fresh read-back proves the requested state. An already-correct
WAN permission is not written again. DHCP changes also check unrelated reservations.

These services are primitives, not an automatic Assist exposure policy. Protect
infrastructure and use a closed allowlist in your own scripts. WAN permission does
not prove internet connectivity and does not block cellular/alternate-gateway access.

Portable tests (no hardware/network):

```sh
python -m unittest tests.test_client_identity tests.test_backend_safety tests.test_documentation
```

The backend/client regression suite currently contains 66 tests. CI targets HA
`2026.9.4` on Python `3.14`; local regression tests also ran on HA `2026.7.4`.
This does not establish full-install compatibility with older HA versions.
Real-device acceptance was read-only; failure, rollback and disruptive controls
were tested with mocks. For bug reports include HA/integration version, router
model/firmware and a sanitized error. Never attach credentials or raw private API data.

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
