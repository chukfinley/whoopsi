"""
Common utilities for Whoop firmware analysis.
Provides r2pipe wrapper, capstone disassembler, angr loader,
function prolog finder, string extractor, and reference scanner.
"""
import struct
import json
import re
import os
from pathlib import Path
from typing import Optional

# Default firmware path
DEFAULT_BIN = str(Path(__file__).parent.parent / "maverick_ambiq_50.35.2.0" / "maverick-50.35.2.0.bin")
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Apollo4 memory map
MRAM_BASE = 0x00018000
SRAM_BASE = 0x10000000
PERIPH_BASE = 0x40000000
VECTOR_TABLE_OFFSET = 0x200

# Known peripheral base addresses (Apollo4 Blue Plus)
APOLLO4_PERIPHERALS = {
    0x40000000: "CLKGEN",
    0x40004000: "PWRCTRL",
    0x40008000: "MCUCTRL",
    0x4000C000: "WDT",
    0x40010000: "GPIO",
    0x40014000: "STIMER",
    0x40018000: "TIMER",
    0x4001C000: "UART0",
    0x40020000: "UART1",
    0x40024000: "UART2",
    0x40028000: "UART3",
    0x40050000: "IOM0",  # I2C/SPI Master 0
    0x40051000: "IOM1",
    0x40052000: "IOM2",
    0x40053000: "IOM3",
    0x40054000: "IOM4",
    0x40055000: "IOM5",
    0x40056000: "IOM6",
    0x40057000: "IOM7",
    0x40058000: "MSPI0",
    0x40059000: "MSPI1",
    0x4005A000: "MSPI2",
    0x40060000: "ADC",
    0x40080000: "AUDADC",
    0x400C0000: "CRYPTO",
    0x400C2000: "SECURITY",
    0x400E0000: "USB",
    0x50000000: "BLE",  # Cordio BLE controller
}

# Known sensor I2C addresses
SENSOR_I2C_ADDRESSES = {
    0x68: "ICM-45686 (IMU 6-axis)",
    0x69: "ICM-45686 (IMU alt addr)",
    0x30: "LP5562 (RGB LED Driver)",
    0x5A: "DRV2625 (Haptic Driver)",
    0x47: "AS6221 (Temperature Sensor)",
    0x48: "AS6221 (Temperature alt)",
    0x0B: "LC709205F (Fuel Gauge)",
    0x55: "LC709205F (Fuel Gauge alt)",
}


def load_firmware(path: str = None) -> bytes:
    """Load firmware binary."""
    path = path or DEFAULT_BIN
    with open(path, "rb") as f:
        return f.read()


# --- Capstone Disassembler ---

def get_capstone_md():
    """Get a Capstone disassembler for ARM Thumb-2."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    md.detail = True
    return md


def disasm(data: bytes, offset: int, length: int = 512, md=None) -> list:
    """Disassemble bytes at offset."""
    if md is None:
        md = get_capstone_md()
    return list(md.disasm(data[offset:offset + length], offset))


def disasm_function(data: bytes, start: int, max_insns: int = 200, md=None) -> list:
    """Disassemble a function from its start, stopping at return."""
    if md is None:
        md = get_capstone_md()
    instrs = list(md.disasm(data[start:start + max_insns * 4], start))
    result = []
    for i, ins in enumerate(instrs):
        result.append(ins)
        if i > 3 and ins.mnemonic in ("pop", "pop.w", "bx") and "pc" in ins.op_str:
            break
        if ins.mnemonic == "bx" and ins.op_str == "lr":
            break
    return result


# --- Function Prolog Finder ---

def find_function_prologs(data: bytes, start: int = 0, end: int = None) -> list:
    """Find ARM Thumb-2 function prologues (PUSH {r4-r7,lr} patterns)."""
    if end is None:
        end = len(data)
    prologs = []
    for off in range(start, end - 1, 2):
        hw = struct.unpack_from("<H", data, off)[0]
        # PUSH {rx, ..., lr} — 0xB5xx
        if (hw & 0xFF00) == 0xB500:
            prologs.append(off)
        # PUSH.W {rx, ..., lr} — 0xE92D 0x4xxx or 0xE92D 0x5xxx
        elif hw == 0xE92D and off + 2 < end:
            hw2 = struct.unpack_from("<H", data, off + 2)[0]
            if hw2 & 0x4000:  # LR bit set
                prologs.append(off)
    return prologs


def find_function_start(data: bytes, addr: int) -> Optional[int]:
    """Search backwards from addr to find the function start."""
    for off in range(2, 8192, 2):
        p = addr - off
        if p < 0:
            return None
        hw = struct.unpack_from("<H", data, p)[0]
        if (hw & 0xFF00) == 0xB500 or hw == 0xE92D:
            return p
    return None


# --- BL Target Finder ---

def find_bl_targets(data: bytes, start: int = 0, end: int = None) -> list:
    """Find all BL (branch with link) instructions and their targets."""
    if end is None:
        end = len(data)
    targets = []
    for off in range(start, min(end, len(data) - 3), 2):
        hw1 = struct.unpack_from("<H", data, off)[0]
        hw2 = struct.unpack_from("<H", data, off + 2)[0]
        if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
            S = (hw1 >> 10) & 1
            imm10 = hw1 & 0x3FF
            J1 = (hw2 >> 13) & 1
            J2 = (hw2 >> 11) & 1
            imm11 = hw2 & 0x7FF
            I1 = 1 - (J1 ^ S)
            I2 = 1 - (J2 ^ S)
            imm32 = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
            if S:
                imm32 |= 0xFE000000
            target = (off + 4 + imm32) & 0xFFFFFFFF
            targets.append((off, target))
    return targets


def find_callers(data: bytes, func_addr: int) -> list:
    """Find all BL instructions targeting func_addr."""
    callers = []
    for call_addr, target in find_bl_targets(data):
        if target == func_addr or target == func_addr + 1:
            callers.append(call_addr)
    return callers


# --- String Extractor ---

def extract_strings(data: bytes, min_length: int = 4, start: int = 0, end: int = None) -> list:
    """Extract ASCII strings from binary data."""
    if end is None:
        end = len(data)
    strings = []
    current = b""
    current_start = 0
    for i in range(start, end):
        b = data[i]
        if 0x20 <= b <= 0x7E or b in (0x0A, 0x0D, 0x09):
            if not current:
                current_start = i
            current += bytes([b])
        else:
            if len(current) >= min_length:
                try:
                    text = current.decode('ascii').strip()
                    if len(text) >= min_length:
                        strings.append({
                            "offset": current_start,
                            "text": text,
                            "length": len(text),
                        })
                except UnicodeDecodeError:
                    pass
            current = b""
    # Don't forget last string
    if len(current) >= min_length:
        try:
            text = current.decode('ascii').strip()
            if len(text) >= min_length:
                strings.append({
                    "offset": current_start,
                    "text": text,
                    "length": len(text),
                })
        except UnicodeDecodeError:
            pass
    return strings


def categorize_string(text: str) -> str:
    """Categorize a firmware string by content."""
    t = text.lower()
    if any(k in t for k in ["ble", "bluetooth", "advertising", "connection", "gatt", "cordio", "cooper"]):
        return "BLE"
    if any(k in t for k in ["sensor", "imu", "ppg", "spo2", "ecg", "accel", "gyro", "sigproc", "afe", "optical"]):
        return "SENSOR"
    if any(k in t for k in ["qp", "qf_", "qactive", "qhsm", "_ao.c", "ao_", "signal", "q_sig"]):
        return "RTOS_QP"
    if any(k in t for k in ["firmware", "update", "flash", "sbl", "ota", "zbin", "image"]):
        return "FIRMWARE_UPDATE"
    if any(k in t for k in ["memfault", "mflt", "debug", "assert", "error", "fault", "crash"]):
        return "DEBUG_MEMFAULT"
    if any(k in t for k in ["i2c", "spi", "uart", "gpio", "iom", "mspi", "adc"]):
        return "HARDWARE"
    if any(k in t for k in ["alarm", "haptic", "led", "ui_", "vibrat"]):
        return "UI"
    if any(k in t for k in ["battery", "fuel", "charge", "wpt", "soc"]):
        return "POWER"
    if any(k in t for k in ["temp", "therm"]):
        return "TEMPERATURE"
    if any(k in t for k in ["./src/", "./modules/", ".c", ".h"]):
        return "SOURCE_PATH"
    if any(k in t for k in ["tag", "rfid", "nfc"]):
        return "TAG_READER"
    return "OTHER"


# --- Reference Scanner ---

def find_string_references(data: bytes, string_offset: int, search_range: tuple = None) -> list:
    """Find code locations that reference a string address via LDR from literal pool."""
    # ARM loads strings via PC-relative LDR from literal pools
    # The literal pool entry contains the absolute address (MRAM_BASE + offset)
    target_addr = MRAM_BASE + string_offset
    refs = []
    start, end = search_range or (0, min(len(data), 0x0A0000))

    # Search for the target address as a 4-byte LE value in literal pools
    addr_bytes = struct.pack("<I", target_addr)
    off = start
    while off < end:
        idx = data.find(addr_bytes, off, end)
        if idx < 0:
            break
        if idx % 4 == 0:  # Literal pool entries are word-aligned
            refs.append(idx)
        off = idx + 4

    return refs


def find_peripheral_references(data: bytes, periph_addr: int, start: int = 0, end: int = None) -> list:
    """Find code that references a peripheral MMIO address."""
    if end is None:
        end = min(len(data), 0x0A0000)
    refs = []
    addr_bytes = struct.pack("<I", periph_addr)
    off = start
    while off < end:
        idx = data.find(addr_bytes, off, end)
        if idx < 0:
            break
        refs.append(idx)
        off = idx + 4
    return refs


# --- Vector Table Parser ---

def parse_vector_table(data: bytes, offset: int = VECTOR_TABLE_OFFSET) -> dict:
    """Parse ARM Cortex-M4 vector table."""
    vector_names = [
        "SP_Init", "Reset_Handler", "NMI_Handler", "HardFault_Handler",
        "MemManage_Handler", "BusFault_Handler", "UsageFault_Handler",
        "Reserved_7", "Reserved_8", "Reserved_9", "Reserved_10",
        "SVC_Handler", "DebugMon_Handler", "Reserved_13",
        "PendSV_Handler", "SysTick_Handler",
    ]
    # Add IRQ handlers
    for i in range(48):
        vector_names.append(f"IRQ{i}_Handler")

    vectors = {}
    for i, name in enumerate(vector_names):
        if offset + i * 4 + 4 > len(data):
            break
        addr = struct.unpack_from('<I', data, offset + i * 4)[0]
        vectors[name] = addr

    return vectors


# --- r2pipe Wrapper ---

class R2Wrapper:
    """Wrapper around r2pipe for firmware analysis."""

    def __init__(self, bin_path: str = None):
        import r2pipe
        self.path = bin_path or DEFAULT_BIN
        self.r2 = r2pipe.open(self.path, flags=["-2"])
        # Configure for ARM Thumb-2
        self.r2.cmd("e asm.arch=arm")
        self.r2.cmd("e asm.bits=16")
        self.r2.cmd("e anal.armthumb=true")
        self.r2.cmd("e cfg.bigendian=false")

    def analyze(self, level: str = "aaa"):
        """Run r2 auto-analysis. 'aaa' is thorough, 'aa' is faster."""
        print(f"  Running r2 analysis ({level})... this may take a few minutes")
        self.r2.cmd(level)

    def get_functions(self) -> list:
        """Get all detected functions."""
        return self.r2.cmdj("aflj") or []

    def get_strings(self) -> list:
        """Get all detected strings."""
        return self.r2.cmdj("izj") or []

    def get_xrefs_to(self, addr: int) -> list:
        """Get cross-references to an address."""
        return self.r2.cmdj(f"axtj @{addr}") or []

    def get_xrefs_from(self, addr: int) -> list:
        """Get cross-references from an address."""
        return self.r2.cmdj(f"axfj @{addr}") or []

    def disasm_at(self, addr: int, count: int = 20) -> str:
        """Disassemble N instructions at address."""
        return self.r2.cmd(f"pd {count} @{addr}")

    def get_function_at(self, addr: int) -> dict:
        """Get function info at address."""
        result = self.r2.cmdj(f"afij @{addr}")
        return result[0] if result else {}

    def get_callgraph(self) -> list:
        """Get function call graph."""
        return self.r2.cmdj("agCj") or []

    def close(self):
        self.r2.quit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# --- angr Wrapper ---

def get_angr_project(bin_path: str = None):
    """Create an angr project for the firmware binary."""
    import angr
    path = bin_path or DEFAULT_BIN
    proj = angr.Project(
        path,
        main_opts={
            'backend': 'blob',
            'arch': 'ARMEL',
            'base_addr': MRAM_BASE,
            'entry_point': MRAM_BASE + VECTOR_TABLE_OFFSET + 4,
        },
        auto_load_libs=False,
    )
    return proj


def get_angr_cfg(proj, fast: bool = True):
    """Generate a control flow graph."""
    if fast:
        return proj.analyses.CFGFast(normalize=True, show_progressbar=True)
    return proj.analyses.CFGEmulated(normalize=True, show_progressbar=True)


# --- Output Helpers ---

def save_output(name: str, data: dict):
    """Save analysis output as JSON."""
    path = OUTPUT_DIR / name
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  Saved: {path} ({path.stat().st_size:,} bytes)")
    return path
