from typing import Final, Any

MIWIFI_API_MAP: Final[dict[str, dict[str, Any]]] = {
    "login": {
        "endpoint": "xqsystem/login",
        "method": "POST",
    },
    "topo_graph": {
        "endpoint": "misystem/topo_graph",
        "method": "GET",
        "use_stok": False,
    },
    "init_info": {
        "endpoint": "xqsystem/init_info",
        "method": "GET",
    },
    "status": {
        "endpoint": "misystem/status",
        "method": "GET",
    },
    "new_status": {
        "endpoint": "misystem/newstatus",
        "method": "GET",
    },
    "mode": {
        "endpoint": "xqnetwork/mode",
        "method": "GET",
    },
    "wifi_ap_signal": {
        "endpoint": "xqnetwork/wifiap_signal",
        "method": "GET",
    },
    "wifi_detail_all": {
        "endpoint": "xqnetwork/wifi_detail_all",
        "method": "GET",
    },
    "wifi_diag_detail_all": {
        "endpoint": "xqnetwork/wifi_diag_detail_all",
        "method": "GET",
    },
    "vpn_status": {
        "endpoint": "xqsystem/vpn_status",
        "method": "GET",
    },
    "wan_info": {
        "endpoint": "xqnetwork/wan_info",
        "method": "GET",
    },
    "reboot": {
        "endpoint": "xqsystem/reboot",
        "method": "GET",
    },
    "led": {
        "endpoint": "misystem/led",
        "method": "GET",
        "payload_map": {
            "state": "on",
        },
    },
    "device_list": {
        "endpoint": "misystem/devicelist",
        "method": "GET",
    },
    "wifi_connect_devices": {
        "endpoint": "xqnetwork/wifi_connect_devices",
        "method": "GET",
    },
    "mac_filter_info": {
        "endpoint": "xqsystem/mac_filter_info",
        "method": "GET",
    },
    "macbind_info": {
        "endpoint": "xqnetwork/macbind_info",
        "method": "GET",
    },
    "rom_update": {
        "endpoint": "xqsystem/check_rom_update",
        "method": "GET",
    },
    "flash_permission": {
        "endpoint": "xqsystem/flash_permission",
        "method": "GET",
    },
    "qos_info": {
        "endpoint": "misystem/qos_info",
        "method": "GET",
    },
    "wifi_timer_info": {
        "endpoint": "xqnetwork/wifi_timer",
        "method": "GET",
    },
    "wifi_macfilter_info": {
        "endpoint": "xqnetwork/wifi_macfilter_info",
        "method": "GET",
    },
    "avaliable_channels": {
        "endpoint": "xqnetwork/avaliable_channels",
        "method": "GET",
        "payload_map": {
            "index": "wifiIndex",
        },
    },
    "set_wifi": {
        "endpoint": "xqnetwork/set_wifi",
        "method": "GET",
        "passthrough": True,
    },
    "set_guest_wifi": {
        "endpoint": "xqnetwork/set_wifi_without_restart",
        "method": "GET",
        "passthrough": True,
    },
    "rom_upgrade": {
        "endpoint": "xqsystem/upgrade_rom",
        "method": "GET",
        "passthrough": True,
        "errors": {
            6: "Download failed",
            7: "No disk space",
            8: "Download failed",
            9: "Upgrade package verification failed",
            10: "Failed to flash",
        },
    },
    "get_port_forward_list": {
        "endpoint": "xqnetwork/portforward",
        "method": "GET",
        "static_params": {"ftype": "1"},
    },
    "add_port_forward": {
        "endpoint": "xqnetwork/add_redirect",
        "method": "POST",
        "payload_map": {
            "name": "name",
            "proto": "proto",
            "ext_port": "sport",
            "fwd_ip": "ip",
            "fwd_port": "dport",
        },
    },
    "delete_port_forward": {
        "endpoint": "xqnetwork/delete_redirect",
        "method": "POST",
        "payload_map": {
            "ext_port": "port",
            "proto": "proto",
        },
    },
    "set_mac_filter": {
        "endpoint": "xqsystem/set_mac_filter",
        "method": "POST",
        "payload_map": {
            "mac": "mac",
            "wan": "wan",
        },
    },
    "mac_bind": {
        "endpoint": "xqnetwork/mac_bind",
        "method": "POST",
        "json_list_payload": True,
        "payload_map": {
            "ip": "ip",
            "mac": "mac",
            "name": "name",
        },
    },
    "mac_unbind": {
        "endpoint": "xqnetwork/mac_unbind",
        "method": "POST",
        "payload_map": {
            "mac": "mac",
        },
    },
    "set_qos_switch": {
        "endpoint": "misystem/qos_switch",
        "method": "POST",
        "payload_map": {
            "on": "on",
        },
    },
    "set_qos_mode": {
        "endpoint": "misystem/qos_mode",
        "method": "POST",
        "payload_map": {
            "mode": "mode",
        },
    },
    "set_band": {
        "endpoint": "misystem/set_band",
        "method": "POST",
        "static_params": {"manual": "1"},
        "payload_map": {
            "upload": "upload",
            "download": "download",
        },
    },
    "set_qos": {
        "endpoint": "misystem/qos_limits",
        "method": "POST",
        "json_list_payload": True,
        "payload_map": {
            "mac": "mac",
            "upload": ("maxup", "{}.00"),
            "download": ("maxdown", "{}.00"),
        },
    },
    "set_wifi_timer": {
        "endpoint": "xqnetwork/set_wifi_timer",
        "method": "POST",
        "payload_map": {
            "start_time": "start",
            "end_time": "end",
            "days": "days",
            "enabled": "enable",
        },
    },
    "set_mac_time": {
        "endpoint": "misystem/set_mac_time",
        "method": "POST",
        "payload_map": {
            "mac": "mac",
            "start_time": "start",
            "end_time": "end",
            "days": "days",
            "enabled": "enable",
        },
    },
    "add_mesh_node": {
        "endpoint": "xqnetwork/add_mesh_node",
        "method": "POST",
        "payload_map": {
            "locate_ip": "locate_ip",
        },
    },
    "set_wifi_macfilter": {
        "endpoint": "xqnetwork/set_wifi_macfilter",
        "method": "POST",
        "payload_map": {
            "model": "model",
            "mac": "mac",
        },
    },
}