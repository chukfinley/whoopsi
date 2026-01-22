/*
 * Linker script for Whoop Maverick (Ambiq Apollo4 Blue Plus)
 *
 * Memory Layout:
 *   MRAM (Flash):  0x00018000 - 0x001FFFFF  (1.9 MB)
 *   SRAM:          0x10000000 - 0x101FFFFF  (2 MB)
 *
 * The firmware image includes a 512-byte OTA header at the start,
 * followed by the vector table at offset 0x200.
 *
 * WARNING: This is for research/analysis only. Do NOT flash without
 *          full understanding of the SBL (Secure Boot Loader) requirements.
 */

MEMORY
{
    /* OTA header occupies first 512 bytes */
    HEADER (r)  : ORIGIN = 0x00018000, LENGTH = 0x200
    /* Code starts after header */
    FLASH  (rx) : ORIGIN = 0x00018200, LENGTH = 0x1E0000 - 0x200
    /* SRAM for stack and data */
    SRAM   (rwx): ORIGIN = 0x10000000, LENGTH = 0x200000
}

/* Stack at top of SRAM, matching original SP = 0x10009C40 */
_stack_top = 0x10009C40;

ENTRY(Reset_Handler)

SECTIONS
{
    /* OTA metadata header — filled by zbin_builder or build script */
    .header : {
        . = 0x200;
    } > HEADER

    /* Vector table must be at 0x200 offset */
    .isr_vector : {
        KEEP(*(.isr_vector))
        . = ALIGN(4);
    } > FLASH

    /* Code */
    .text : {
        *(.text)
        *(.text*)
        *(.rodata)
        *(.rodata*)
        . = ALIGN(4);
        _etext = .;
    } > FLASH

    /* Initialized data (copied from flash to SRAM) */
    .data : AT(_etext) {
        _sdata = .;
        *(.data)
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > SRAM

    /* Zero-initialized data */
    .bss : {
        _sbss = .;
        *(.bss)
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > SRAM

    /* Stack */
    .stack (NOLOAD) : {
        . = ALIGN(8);
        . = . + 0x2000; /* 8KB stack */
        _stack = .;
    } > SRAM
}
