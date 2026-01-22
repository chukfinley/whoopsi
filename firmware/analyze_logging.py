#!/usr/bin/env python3
"""The strings use a format like '%6llu: Firmware Load (0x8E)' suggesting a 
timestamped logging system. The strings are in a data section (0x0A0000-0x0C0000).
Let's understand how these strings get into the binary and if they're referenced
at all, or if they're just debug symbols/metadata.

Key observation: 0x0B0000-0x0C0000 is 92% ASCII (the string region). 
The string region might be a SEPARATE firmware component (e.g., a "string table" 
partition) that's concatenated into this binary but NOT directly referenced by code.

The actual BLE firmware code (RTOS task) would reference these strings through
a logging API that uses string IDs. Let's look for the actual string ID mapping.

Alternative: These could be printf-style strings embedded in a logging framework 
where the compiler generates a string table with metadata. Segger RTT or similar.
"""

import struct

from pathlib import Path
BIN_PATH = str(Path(__file__).parent / "maverick_ambiq_50.35.2.0/maverick-50.35.2.0.bin")
with open(BIN_PATH, "rb") as f:
    data = f.read()

# Let's look at the structure of the string region more carefully.
# Strings like "%6llu: Firmware Load (0x8E)" - the %6llu suggests a uint64 timestamp.
# There might be structured entries: {id, level, string_offset} or similar.

# Let's examine the bytes BETWEEN strings in the string region.
# Are there structured headers between null-terminated strings?
print("=== STRING REGION STRUCTURE (0x0B4800-0x0B4C00) ===")
# This covers the firmware update strings
off = 0x0B4800
while off < 0x0B4C00:
    # Find the next null terminator
    end = data.index(0, off) if 0 in data[off:off+200] else off+200
    s = data[off:end]
    try:
        decoded = s.decode('ascii')
        if decoded.isprintable() and len(decoded) > 2:
            # Check bytes before this string
            pre = data[max(0,off-8):off]
            print(f"  0x{off:06X}: [{pre.hex()}] \"{decoded[:70]}\"")
            off = end + 1
            continue
    except:
        pass
    off += 1

# Let's also check if these strings are in a known format like Zephyr's logging
# or NRF logging. The "%6llu:" prefix is interesting - suggests it's a formatted
# log message already, not a format string.
# Wait - these ARE format strings! "%6llu: %s: CRC of update image passed"
# The %6llu is for a timestamp, %s for the module name.

# So the question remains: how does code find these strings?
# Let's search for a string table descriptor/metadata.

# Check the region just before the string data starts (around 0x0A0000)
print("\n=== REGION BEFORE STRINGS (0x09F000-0x0A0100) ===")
for off in range(0x09F000, 0x0A0100, 4):
    val = struct.unpack_from("<I", data, off)[0]
    if 0x0A0000 <= val <= 0x0D0000:  # Points into string region
        try:
            end = data.index(0, val)
            s = data[val:min(val+40, end)].decode('ascii')
            if len(s) > 3 and s.isprintable():
                print(f"  0x{off:06X}: -> 0x{val:06X} \"{s[:50]}\"")
        except:
            pass

# What about the data region at 0x0A8760 (the big 414-entry "table" found earlier)?
# That was mostly garbage. Let's check what's actually there.
print("\n=== EXAMINING 0x0A8700-0x0A8800 ===")
for off in range(0x0A8700, 0x0A8800, 4):
    val = struct.unpack_from("<I", data, off)[0]
    print(f"  0x{off:06X}: 0x{val:08X}", end="")
    if 0x0A0000 <= val <= 0x170000 and val < len(data):
        try:
            end = data.index(0, val)
            s = data[val:min(val+30, end)].decode('ascii')
            if len(s) > 2 and all(c.isprintable() or c in '\n\r' for c in s):
                print(f"  \"{s[:30]}\"", end="")
        except:
            pass
    print()

# Let's try a completely different approach: look for the string "0x8E" as bytes
# in the code region (not string region). If code computes string references by
# some encoding, maybe we can find embedded string fragments.
print("\n=== SEARCHING FOR '0x8E' TEXT IN CODE REGION ===")
target = b"0x8E"
pos = 0
while True:
    pos = data.find(target, pos)
    if pos == -1: break
    context = data[max(0,pos-10):pos+20]
    try:
        decoded = context.decode('ascii', errors='replace')
        print(f"  0x{pos:06X}: ...{decoded}...")
    except:
        pass
    pos += 1

# Let's check: is there a Memfault or similar diagnostic framework?
# Search for "memfault" in binary
print("\n=== KNOWN FRAMEWORKS ===")
for framework in [b"memfault", b"Memfault", b"SEGGER", b"RTT", b"nrf_log", b"Zephyr", 
                   b"FreeRTOS", b"log_msg", b"LOG_MODULE"]:
    pos = data.find(framework)
    if pos != -1:
        print(f"  '{framework.decode()}' found at 0x{pos:06X}")

