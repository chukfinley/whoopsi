#!/usr/bin/env python3
"""
WHOOP Strap Bluetooth Protocol Packet Decoder
=============================================

Decodes the WHOOP BLE protocol packets captured from HCI snoop logs.
Analyzes TX WRITE_REQ (handle 0x099b) and RX NOTIFICATION (handle 0x099d).

Packet Structure (Maverick / WHOOP 4.0):
=========================================

  Offset  Size   Field             Description
  ------  ----   -----             -----------
  0       1      SOF               Start of Frame, always 0xAA
  1       1      Revision          Protocol revision, 0x01 for Maverick
  2-3     2      Length            Payload length (LE uint16), from byte 8 to end (incl CRC32)
  4-5     2      Routing           Direction/routing: 0x0001=App->Strap, 0x0100=Strap->App
  6-7     2      Header CRC        CRC-16/MODBUS of bytes 0-5, stored little-endian
  --- header ends, payload begins ---
  8       1      Command Type      0x23=COMMAND (to strap), 0x24=RESPONSE (from strap)
  9       1      Sequence          Incrementing sequence number (wraps at 0xFF)
  10      1      Command Code      Identifies the command/response
  11+     N      Parameters        Command-specific parameters
  last 4  4      CRC32             CRC32 of payload (bytes 8..end-4), init=0, stored LE

CRC Algorithms:
  Header CRC: CRC-16/MODBUS (poly=0xA001 reflected, init=0xFFFF) on bytes 0-5, stored LE
  Payload CRC: Standard CRC32 (zlib) on bytes 8..end-4, init=0, stored LE
"""

import struct
import binascii
import sys
import subprocess
import re
import os
from collections import defaultdict

# -- Known command codes --

COMMAND_CODES = {
    0x0E: "ALARM_CONFIG",
    0x13: "CONFIGURE_SENSOR",
    0x16: "GET_STRAP_STATUS",
    0x17: "READ_FLASH_DATA",
    0x22: "HISTORY_QUERY",
    0x42: "HISTORY_RANGE_REQUEST",
    0x8D: "GET_DEVICE_NAME",
    0x91: "GET_DEVICE_SERIAL",
}

COMMAND_TYPES = {
    0x23: "COMMAND",
    0x24: "RESPONSE",
}

ROUTING = {
    (0x00, 0x01): "App -> Strap",
    (0x01, 0x00): "Strap -> App",
}


# -- CRC functions --

def crc16_modbus(data: bytes) -> int:
    """CRC-16/MODBUS: reflected poly 0xA001, init 0xFFFF."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def crc32_payload(data: bytes) -> int:
    """Standard CRC32 with init=0 (not the default 0xFFFFFFFF)."""
    return binascii.crc32(data, 0) & 0xFFFFFFFF


# -- Packet decoder --

def decode_packet(hex_str: str, direction: str = "TX", timestamp: str = "") -> dict:
    """Decode a single WHOOP BLE packet from hex string."""
    raw = bytes.fromhex(hex_str)
    pkt = {"raw": raw, "hex": hex_str, "direction": direction, "timestamp": timestamp}

    if len(raw) < 12:
        pkt["error"] = f"Packet too short ({len(raw)} bytes, min 12)"
        return pkt

    # -- Header (bytes 0-7) --
    pkt["sof"] = raw[0]
    pkt["revision"] = raw[1]
    pkt["length"] = struct.unpack_from("<H", raw, 2)[0]
    pkt["routing"] = (raw[4], raw[5])
    pkt["header_crc_stored"] = struct.unpack_from("<H", raw, 6)[0]

    # Verify header CRC
    header_crc_calc = crc16_modbus(raw[0:6])
    pkt["header_crc_calc"] = header_crc_calc
    pkt["header_crc_ok"] = header_crc_calc == pkt["header_crc_stored"]

    # -- Payload (bytes 8 .. end-4) --
    payload = raw[8:-4]
    pkt["payload_crc_stored"] = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    payload_crc_calc = crc32_payload(payload)
    pkt["payload_crc_calc"] = payload_crc_calc
    pkt["payload_crc_ok"] = payload_crc_calc == pkt["payload_crc_stored"]

    # Length check: stated length should equal len(raw) - 8 (header size)
    expected_len = len(raw) - 8
    pkt["length_ok"] = pkt["length"] == expected_len

    # -- Command fields --
    pkt["cmd_type"] = payload[0] if len(payload) > 0 else None
    pkt["sequence"] = payload[1] if len(payload) > 1 else None
    pkt["cmd_code"] = payload[2] if len(payload) > 2 else None
    pkt["cmd_params"] = payload[3:] if len(payload) > 3 else b""

    return pkt


def format_routing(routing: tuple) -> str:
    return ROUTING.get(routing, f"Unknown({routing[0]:02x}{routing[1]:02x})")


def decode_cmd_params(pkt: dict) -> str:
    """Return a human-readable description of command-specific parameters."""
    code = pkt.get("cmd_code")
    params = pkt.get("cmd_params", b"")
    direction = pkt["direction"]

    if code == 0x17:  # READ_FLASH_DATA
        if direction == "TX" and len(params) >= 9:
            subcmd = params[0]
            offset = struct.unpack_from("<I", params, 1)[0]
            length = struct.unpack_from("<I", params, 5)[0]
            return f"subcmd={subcmd:02x} offset=0x{offset:08x} length={length}"
        elif direction == "RX" and len(params) >= 1:
            status = params[0]
            return f"status={status:02x} ({'OK' if status == 1 else 'FAIL'})"

    elif code == 0x91:  # GET_DEVICE_SERIAL
        if direction == "TX":
            return f"subcmd={params[0]:02x}" if params else ""
        elif direction == "RX" and len(params) > 1:
            try:
                ascii_parts = []
                i = 0
                while i < len(params):
                    if 0x20 <= params[i] <= 0x7E:
                        start = i
                        while i < len(params) and 0x20 <= params[i] <= 0x7E:
                            i += 1
                        s = params[start:i].decode("ascii")
                        if len(s) >= 3:
                            ascii_parts.append(s)
                    else:
                        i += 1
                if ascii_parts:
                    return f"strings: {ascii_parts}"
            except Exception:
                pass
            return f"data[{len(params)}]={params[:16].hex()}..."

    elif code == 0x8D:  # GET_DEVICE_NAME
        if direction == "TX":
            return f"subcmd={params[0]:02x}" if params else ""
        elif direction == "RX" and len(params) > 2:
            try:
                name_bytes = params[3:]
                name = name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")
                return f"name=\"{name}\" hw_rev={params[0]:02x} fw_fields={params[1]:02x}.{params[2]:02x}"
            except Exception:
                pass

    elif code == 0x22:  # HISTORY_QUERY
        if direction == "TX":
            return f"subcmd={params[0]:02x}" if params else ""
        elif direction == "RX" and len(params) >= 1:
            status = params[0]
            if len(params) >= 2:
                sub = params[1]
                return f"status={status:02x} sub={sub:02x} data={params[2:].hex()}"
            return f"status={status:02x}"

    elif code == 0x42:  # HISTORY_RANGE_REQUEST
        if direction == "TX" and len(params) >= 1:
            subcmd = params[0]
            if len(params) >= 17:
                ts1 = struct.unpack_from("<I", params, 1)[0]
                ts2 = struct.unpack_from("<I", params, 5)[0]
                return (f"subcmd={subcmd:02x} "
                        f"timestamp1=0x{ts1:08x} ({ts1}) "
                        f"timestamp2=0x{ts2:08x} ({ts2}) "
                        f"rest={params[9:].hex()}")
            return f"subcmd={subcmd:02x} data={params[1:].hex()}"
        elif direction == "RX":
            return f"status={params[0]:02x} data={params[1:].hex()}" if params else ""

    elif code == 0x13:  # CONFIGURE_SENSOR
        if direction == "TX" and len(params) >= 1:
            subcmd = params[0]
            if len(params) >= 5:
                val = struct.unpack_from("<I", params, 1)[0]
                return f"subcmd={subcmd:02x} value=0x{val:08x} rest={params[5:].hex()}"
            return f"subcmd={subcmd:02x} data={params[1:].hex()}"
        elif direction == "RX":
            return f"status={params[0]:02x}" if params else ""

    elif code == 0x16:  # GET_STRAP_STATUS
        if direction == "TX":
            return f"subcmd={params[0]:02x}" if params else ""
        elif direction == "RX" and len(params) >= 2:
            return f"status={params[0]:02x} sub={params[1]:02x} data={params[2:].hex()}"

    elif code == 0x0E:  # ALARM_CONFIG
        if direction == "TX":
            return f"subcmd={params[0]:02x}" if params else ""
        elif direction == "RX" and len(params) >= 1:
            return f"status={params[0]:02x}"

    if params:
        return f"raw={params.hex()}"
    return ""


# -- Extraction from HCI log --

def extract_packets(hci_log_path: str, parser_path: str) -> list:
    """Run parse_hci.py and extract relevant packets."""
    result = subprocess.run(
        [sys.executable, parser_path, hci_log_path],
        capture_output=True, text=True
    )
    output = result.stdout + result.stderr
    packets = []

    for m in re.finditer(
        r"(\d+:\d+:\d+\.\d+)\s+TX WRITE_REQ\s+handle=0x099b\s+value=([0-9a-f]+)",
        output
    ):
        packets.append(("TX", m.group(1), m.group(2)))

    for m in re.finditer(
        r"(\d+:\d+:\d+\.\d+)\s+RX NOTIFICATION\s+handle=0x099d\s+len=\d+\s+value=([0-9a-f]+)",
        output
    ):
        packets.append(("RX", m.group(1), m.group(2)))

    packets.sort(key=lambda x: x[1])
    return packets


# -- Display functions --

def print_header():
    print("=" * 120)
    print("WHOOP BLE Protocol Packet Decoder")
    print("=" * 120)
    print()
    print("PROTOCOL STRUCTURE:")
    print("  Bytes 0-7:  Header [SOF(1) | Rev(1) | Length(2,LE) | Routing(2) | HeaderCRC16(2,LE)]")
    print("  Bytes 8+:   Payload [CmdType(1) | Seq(1) | CmdCode(1) | Params(N) | CRC32(4,LE)]")
    print()
    print("  Header CRC: CRC-16/MODBUS on bytes 0-5, stored LE at bytes 6-7")
    print("  Payload CRC: CRC32 (init=0) on bytes 8..end-4, stored LE as last 4 bytes")
    print()
    print("  CmdType: 0x23=COMMAND (app->strap), 0x24=RESPONSE (strap->app)")
    print("  Routing: 0x0001=App->Strap, 0x0100=Strap->App")
    print()


def print_packet_detailed(pkt: dict, index: int):
    raw = pkt["raw"]
    d = pkt["direction"]
    ts = pkt["timestamp"]

    cmd_type_name = COMMAND_TYPES.get(pkt["cmd_type"], f"0x{pkt['cmd_type']:02x}")
    cmd_code_name = COMMAND_CODES.get(pkt["cmd_code"], f"0x{pkt['cmd_code']:02x}")
    routing_name = format_routing(pkt["routing"])

    hcrc_status = "OK" if pkt["header_crc_ok"] else "FAIL"
    pcrc_status = "OK" if pkt["payload_crc_ok"] else "FAIL"
    len_status = "OK" if pkt["length_ok"] else "FAIL"

    arrow = ">>>" if d == "TX" else "<<<"
    dir_label = "TX CMD " if d == "TX" else "RX RESP"

    print(f"--- Packet #{index:03d} {arrow} {dir_label} -- {ts} {'─' * 60}")
    print(f"  Raw ({len(raw)} bytes): {raw.hex()}")
    print()

    print(f"  HEADER (bytes 0-7):")
    print(f"    [0]    SOF          = 0x{pkt['sof']:02X}")
    print(f"    [1]    Revision     = 0x{pkt['revision']:02X}")
    print(f"    [2-3]  Length       = 0x{pkt['length']:04X} ({pkt['length']}) [{len_status}]")
    print(f"    [4-5]  Routing      = {pkt['routing'][0]:02X} {pkt['routing'][1]:02X}  ({routing_name})")
    print(f"    [6-7]  Header CRC16 = 0x{pkt['header_crc_stored']:04X} [{hcrc_status}]")

    print(f"  PAYLOAD (bytes 8-{len(raw)-5}):")
    print(f"    [8]    Cmd Type     = 0x{pkt['cmd_type']:02X}  ({cmd_type_name})")
    print(f"    [9]    Sequence     = 0x{pkt['sequence']:02X}  ({pkt['sequence']})")
    print(f"    [10]   Cmd Code     = 0x{pkt['cmd_code']:02X}  ({cmd_code_name})")

    if pkt["cmd_params"]:
        params_hex = pkt["cmd_params"].hex()
        print(f"    [11..] Parameters   = {params_hex}")

    decoded = decode_cmd_params(pkt)
    if decoded:
        print(f"    >>> Decoded: {decoded}")

    print(f"  CRC32 (last 4 bytes):")
    print(f"    [{len(raw)-4}-{len(raw)-1}]  Payload CRC  = 0x{pkt['payload_crc_stored']:08X} [{pcrc_status}]")
    print()


def print_summary(packets: list):
    """Print summary statistics."""
    print("=" * 120)
    print("SUMMARY")
    print("=" * 120)

    tx_count = sum(1 for p in packets if p["direction"] == "TX")
    rx_count = sum(1 for p in packets if p["direction"] == "RX")
    print(f"\n  Total packets: {len(packets)} (TX: {tx_count}, RX: {rx_count})")

    hcrc_ok = sum(1 for p in packets if p.get("header_crc_ok"))
    pcrc_ok = sum(1 for p in packets if p.get("payload_crc_ok"))
    print(f"  Header CRC16 valid: {hcrc_ok}/{len(packets)}")
    print(f"  Payload CRC32 valid: {pcrc_ok}/{len(packets)}")

    print("\n  Command Code Frequency:")
    code_counts = defaultdict(lambda: {"TX": 0, "RX": 0})
    for p in packets:
        code = p.get("cmd_code")
        if code is not None:
            code_counts[code][p["direction"]] += 1

    for code in sorted(code_counts):
        name = COMMAND_CODES.get(code, "UNKNOWN")
        counts = code_counts[code]
        print(f"    0x{code:02X} ({name:25s}):  TX={counts['TX']:3d}  RX={counts['RX']:3d}")

    tx_seqs = [p["sequence"] for p in packets if p["direction"] == "TX" and p.get("sequence") is not None]
    rx_seqs = [p["sequence"] for p in packets if p["direction"] == "RX" and p.get("sequence") is not None]
    if tx_seqs:
        print(f"\n  TX Sequence range: 0x{min(tx_seqs):02X} - 0x{max(tx_seqs):02X}")
    if rx_seqs:
        print(f"  RX Sequence range: 0x{min(rx_seqs):02X} - 0x{max(rx_seqs):02X}")

    print("\n  READ_FLASH_DATA (0x17) address map:")
    for p in packets:
        if p.get("cmd_code") == 0x17 and p["direction"] == "TX":
            params = p.get("cmd_params", b"")
            if len(params) >= 9:
                subcmd = params[0]
                offset = struct.unpack_from("<I", params, 1)[0]
                length = struct.unpack_from("<I", params, 5)[0]
                print(f"    seq={p['sequence']:02X}  offset=0x{offset:08X}  length={length:4d}  "
                      f"(addr range: 0x{offset:08X} - 0x{offset + length - 1:08X})")

    print("\n  Request-Response Pairing (by sequence number):")
    tx_by_seq = {}
    rx_by_seq = {}
    for p in packets:
        seq = p.get("sequence")
        if seq is None:
            continue
        if p["direction"] == "TX":
            tx_by_seq[seq] = p
        else:
            rx_by_seq.setdefault(seq, []).append(p)

    for seq in sorted(set(list(tx_by_seq.keys()) + list(rx_by_seq.keys()))):
        tx = tx_by_seq.get(seq)
        rxs = rx_by_seq.get(seq, [])
        tx_cmd = COMMAND_CODES.get(tx["cmd_code"], f"0x{tx['cmd_code']:02x}") if tx else "---"
        rx_cmds = ", ".join(COMMAND_CODES.get(r["cmd_code"], f"0x{r['cmd_code']:02x}") for r in rxs) if rxs else "---"
        rx_sizes = ", ".join(str(len(r["raw"])) for r in rxs)
        print(f"    seq=0x{seq:02X}: TX={tx_cmd:25s} -> RX=[{rx_cmds}] (sizes: {rx_sizes})")


def print_conversation(packets: list):
    """Print packets as a conversation flow."""
    print()
    print("=" * 120)
    print("CONVERSATION FLOW")
    print("=" * 120)

    for i, pkt in enumerate(packets):
        d = pkt["direction"]
        ts = pkt["timestamp"]
        cmd_name = COMMAND_CODES.get(pkt.get("cmd_code", 0), f"0x{pkt.get('cmd_code', 0):02x}")
        seq = pkt.get("sequence", 0)
        decoded = decode_cmd_params(pkt)

        if d == "TX":
            print(f"  {ts}  [{seq:02X}]  APP --> STRAP  {cmd_name}")
        else:
            print(f"  {ts}  [{seq:02X}]              STRAP --> APP  {cmd_name}  ({len(pkt['raw'])} bytes)")

        if decoded:
            pad = "                         " if d == "TX" else "                                        "
            print(f"  {pad} {decoded}")


def print_protocol_spec():
    print()
    print("=" * 120)
    print("PROTOCOL SPECIFICATION (CONFIRMED BY CRC VERIFICATION)")
    print("=" * 120)
    print("""
  WHOOP Maverick (4.0) BLE Packet Format
  =======================================

  +-------+------+--------+---------+--------------+-------------------------------+---------+
  | SOF   | Rev  | Length | Routing | Header CRC16 | Payload                       | CRC32   |
  | 1B    | 1B   | 2B LE  | 2B      | 2B LE        | N bytes                       | 4B LE   |
  | 0xAA  | 0x01 |        |         |              |                               |         |
  +-------+------+--------+---------+--------------+-------------------------------+---------+
  |<---- header (8 bytes) -------->||<------------- payload (Length bytes) ----------------->|

  Payload structure:
  +----------+------+----------+------------+---------+
  | Cmd Type | Seq  | Cmd Code | Parameters | CRC32   |
  | 1B       | 1B   | 1B       | variable   | 4B LE   |
  | 0x23/24  |      |          |            |         |
  +----------+------+----------+------------+---------+

  Header CRC16: CRC-16/MODBUS (poly=0x8005 reflected as 0xA001, init=0xFFFF)
                Computed over bytes 0-5, stored LE at bytes 6-7

  Payload CRC32: Standard CRC32 (poly=0xEDB88320, init=0x00000000)
                 Computed over payload bytes (8..end-4), stored LE as last 4 bytes

  Length field: Number of bytes from byte 8 to end of packet (payload + CRC32)

  Routing:
    0x00 0x01 = App -> Strap (TX commands)
    0x01 0x00 = Strap -> App (RX responses)

  Command Types:
    0x23 = COMMAND  (sent by app)
    0x24 = RESPONSE (sent by strap)

  Known Command Codes:
    0x0E = ALARM_CONFIG         - Configure alarm settings
    0x13 = CONFIGURE_SENSOR     - Configure sensor parameters
    0x16 = GET_STRAP_STATUS     - Query strap status / battery info
    0x17 = READ_FLASH_DATA      - Read from strap flash memory
           TX params: [subcmd(1)] [offset(4,LE)] [length(4,LE)]
           RX params: [status(1)] -- status 0x01 = success
    0x22 = HISTORY_QUERY        - Query history / calculated metrics
    0x42 = HISTORY_RANGE_REQ    - Request history for a given time range
           TX params: [subcmd(1)] [timestamp1(4,LE)] [timestamp2(4,LE)] [flags...]
    0x8D = GET_DEVICE_NAME      - Get device name (returns "Whoop")
    0x91 = GET_DEVICE_SERIAL    - Get serial number and hardware info
           Response contains ASCII serial number and hardware identifiers
""")


# -- Main --

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_log = os.path.join(script_dir, "extracted_data", "btsnoop_latest.log")
    default_parser = os.path.join(script_dir, "parse_hci.py")

    hci_log = sys.argv[1] if len(sys.argv) > 1 else default_log
    parser = sys.argv[2] if len(sys.argv) > 2 else default_parser

    if not os.path.exists(hci_log):
        print(f"Error: HCI log not found: {hci_log}")
        sys.exit(1)
    if not os.path.exists(parser):
        print(f"Error: Parser not found: {parser}")
        sys.exit(1)

    print(f"Extracting packets from: {hci_log}")
    print(f"Using parser: {parser}")
    print()

    raw_packets = extract_packets(hci_log, parser)
    print(f"Found {len(raw_packets)} packets on handles 0x099b/0x099d")
    print()

    decoded = []
    for direction, timestamp, hex_str in raw_packets:
        pkt = decode_packet(hex_str, direction, timestamp)
        decoded.append(pkt)

    print_header()

    print("=" * 120)
    print("DETAILED PACKET BREAKDOWN")
    print("=" * 120)
    for i, pkt in enumerate(decoded):
        if "error" in pkt:
            print(f"--- Packet #{i:03d} ERROR: {pkt['error']}")
            continue
        print_packet_detailed(pkt, i)

    print_conversation(decoded)
    print_summary(decoded)
    print_protocol_spec()


if __name__ == "__main__":
    main()
