#!/usr/bin/env python3
"""Parse Android btsnoop_hci.log to extract GATT ATT operations for Whoop BLE reverse engineering."""

import struct
import sys
from datetime import datetime

# ATT opcodes we care about
ATT_OPCODES = {
    0x01: "ERROR_RSP",
    0x02: "MTU_REQ",
    0x03: "MTU_RSP",
    0x04: "FIND_INFO_REQ",
    0x05: "FIND_INFO_RSP",
    0x08: "READ_BY_TYPE_REQ",
    0x09: "READ_BY_TYPE_RSP",
    0x0A: "READ_REQ",
    0x0B: "READ_RSP",
    0x10: "READ_BY_GROUP_REQ",
    0x11: "READ_BY_GROUP_RSP",
    0x12: "WRITE_REQ",
    0x13: "WRITE_RSP",
    0x16: "PREPARE_WRITE_REQ",
    0x17: "PREPARE_WRITE_RSP",
    0x18: "EXECUTE_WRITE_REQ",
    0x19: "EXECUTE_WRITE_RSP",
    0x1B: "HANDLE_VALUE_NTF",
    0x1D: "HANDLE_VALUE_IND",
    0x1E: "HANDLE_VALUE_CFM",
    0x52: "WRITE_CMD",
    0xD2: "SIGNED_WRITE_CMD",
}

# Whoop handle mapping (populated from service discovery)
handle_names = {}


def parse_btsnoop(filename):
    with open(filename, "rb") as f:
        # Header
        magic = f.read(8)
        if magic != b"btsnoop\x00":
            print(f"Not a btsnoop file: {magic}")
            return

        version = struct.unpack(">I", f.read(4))[0]
        datalink = struct.unpack(">I", f.read(4))[0]
        print(f"btsnoop v{version}, datalink={datalink}")

        packets = []
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break

            orig_len, inc_len, flags, drops, ts_hi, ts_lo = struct.unpack(
                ">IIIIII", hdr
            )

            # Timestamp: microseconds since 0000-01-01
            ts_us = (ts_hi << 32) | ts_lo
            # Convert to unix timestamp
            ts_unix = (ts_us - 0x00DCDDB30F2F8000) / 1_000_000

            data = f.read(inc_len)
            if len(data) < inc_len:
                break

            # flags: bit 0 = direction (0=sent, 1=received), bit 1 = command/event
            direction = "TX" if (flags & 1) == 0 else "RX"
            is_command = (flags & 2) != 0

            packets.append((ts_unix, direction, is_command, data))

        print(f"Total packets: {len(packets)}")
        print()

        # Parse ATT operations from ACL data packets
        for ts, direction, is_cmd, data in packets:
            if len(data) < 1:
                continue

            # HCI packet type indicator
            pkt_type = data[0] if datalink == 1002 else 0
            payload = data[1:] if datalink == 1002 else data

            # We want ACL data (type 0x02)
            if pkt_type != 0x02:
                continue

            if len(payload) < 4:
                continue

            # ACL header: handle(2) + length(2)
            acl_handle = struct.unpack("<H", payload[0:2])[0] & 0x0FFF
            acl_len = struct.unpack("<H", payload[2:4])[0]
            acl_data = payload[4:]

            if len(acl_data) < 4:
                continue

            # L2CAP header: length(2) + CID(2)
            l2cap_len = struct.unpack("<H", acl_data[0:2])[0]
            l2cap_cid = struct.unpack("<H", acl_data[2:4])[0]
            l2cap_data = acl_data[4:]

            # CID 0x0004 = ATT
            if l2cap_cid != 0x0004:
                continue

            if len(l2cap_data) < 1:
                continue

            att_opcode = l2cap_data[0]
            att_data = l2cap_data[1:]
            att_name = ATT_OPCODES.get(att_opcode, f"UNKNOWN(0x{att_opcode:02x})")

            ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]

            # Parse based on opcode
            if att_opcode == 0x12 and len(att_data) >= 2:  # WRITE_REQ
                handle = struct.unpack("<H", att_data[0:2])[0]
                value = att_data[2:]
                hname = handle_names.get(handle, f"0x{handle:04x}")
                print(
                    f"{ts_str} {direction} WRITE_REQ    handle={hname} value={value.hex()}"
                )

            elif att_opcode == 0x52 and len(att_data) >= 2:  # WRITE_CMD (no response)
                handle = struct.unpack("<H", att_data[0:2])[0]
                value = att_data[2:]
                hname = handle_names.get(handle, f"0x{handle:04x}")
                print(
                    f"{ts_str} {direction} WRITE_CMD    handle={hname} value={value.hex()}"
                )

            elif att_opcode == 0x1B and len(att_data) >= 2:  # HANDLE_VALUE_NTF
                handle = struct.unpack("<H", att_data[0:2])[0]
                value = att_data[2:]
                hname = handle_names.get(handle, f"0x{handle:04x}")
                print(
                    f"{ts_str} {direction} NOTIFICATION handle={hname} len={len(value)} value={value[:40].hex()}{'...' if len(value) > 40 else ''}"
                )

            elif att_opcode == 0x1D and len(att_data) >= 2:  # HANDLE_VALUE_IND
                handle = struct.unpack("<H", att_data[0:2])[0]
                value = att_data[2:]
                hname = handle_names.get(handle, f"0x{handle:04x}")
                print(
                    f"{ts_str} {direction} INDICATION   handle={hname} len={len(value)} value={value[:40].hex()}{'...' if len(value) > 40 else ''}"
                )

            elif att_opcode == 0x0B and len(att_data) >= 0:  # READ_RSP
                print(f"{ts_str} {direction} READ_RSP     value={att_data.hex()}")

            elif att_opcode == 0x09 and len(att_data) >= 1:  # READ_BY_TYPE_RSP
                # Service/characteristic discovery
                pair_len = att_data[0]
                pairs = att_data[1:]
                i = 0
                while i + pair_len <= len(pairs):
                    pair = pairs[i : i + pair_len]
                    if (
                        pair_len >= 7
                    ):  # handle(2) + properties(1) + value_handle(2) + uuid
                        attr_handle = struct.unpack("<H", pair[0:2])[0]
                        props = pair[2]
                        val_handle = struct.unpack("<H", pair[3:5])[0]
                        uuid_bytes = pair[5:]
                        if len(uuid_bytes) == 2:
                            uuid = f"0x{struct.unpack('<H', uuid_bytes)[0]:04x}"
                        elif len(uuid_bytes) == 16:
                            uuid = "-".join(
                                [
                                    uuid_bytes[12:16][::-1].hex(),
                                    uuid_bytes[10:12][::-1].hex(),
                                    uuid_bytes[8:10][::-1].hex(),
                                    uuid_bytes[6:8][::-1].hex(),
                                    uuid_bytes[0:6][::-1].hex(),
                                ]
                            )
                        else:
                            uuid = uuid_bytes.hex()

                        # Map known Whoop UUIDs
                        name = uuid
                        if "fd4b0002" in uuid:
                            name = "CMD_TO_STRAP"
                        elif "fd4b0003" in uuid:
                            name = "CMD_FROM_STRAP"
                        elif "fd4b0004" in uuid:
                            name = "EVENTS_FROM_STRAP"
                        elif "fd4b0005" in uuid:
                            name = "DATA_FROM_STRAP"
                        elif "fd4b0007" in uuid:
                            name = "MEMFAULT"

                        handle_names[val_handle] = name
                        print(
                            f"{ts_str} {direction} CHAR_DISC    handle=0x{val_handle:04x} props=0x{props:02x} uuid={name}"
                        )
                    i += pair_len

            elif att_opcode == 0x02 and len(att_data) >= 2:  # MTU_REQ
                mtu = struct.unpack("<H", att_data[0:2])[0]
                print(f"{ts_str} {direction} MTU_REQ      mtu={mtu}")

            elif att_opcode == 0x03 and len(att_data) >= 2:  # MTU_RSP
                mtu = struct.unpack("<H", att_data[0:2])[0]
                print(f"{ts_str} {direction} MTU_RSP      mtu={mtu}")


if __name__ == "__main__":
    filename = (
        sys.argv[1] if len(sys.argv) > 1 else "extracted_data/btsnoop_official.log"
    )
    parse_btsnoop(filename)
