/*
 * Minimal startup code for Ambiq Apollo4 Blue Plus (ARM Cortex-M4F)
 *
 * This provides:
 *   - Vector table with Reset_Handler entry
 *   - .data and .bss initialization
 *   - SystemInit and main() calls
 *
 * WARNING: Research/analysis only. Do NOT flash on real hardware
 *          without full SBL analysis.
 */

#include <stdint.h>

/* Linker symbols */
extern uint32_t _etext;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;
extern uint32_t _stack_top;

/* Forward declarations */
void Reset_Handler(void);
void Default_Handler(void);
void NMI_Handler(void) __attribute__((weak, alias("Default_Handler")));
void HardFault_Handler(void) __attribute__((weak, alias("Default_Handler")));
void MemManage_Handler(void) __attribute__((weak, alias("Default_Handler")));
void BusFault_Handler(void) __attribute__((weak, alias("Default_Handler")));
void UsageFault_Handler(void) __attribute__((weak, alias("Default_Handler")));
void SVC_Handler(void) __attribute__((weak, alias("Default_Handler")));
void DebugMon_Handler(void) __attribute__((weak, alias("Default_Handler")));
void PendSV_Handler(void) __attribute__((weak, alias("Default_Handler")));
void SysTick_Handler(void) __attribute__((weak, alias("Default_Handler")));

extern int main(void);

/* Vector table — placed at offset 0x200 in the firmware image */
__attribute__((section(".isr_vector"), used))
const uint32_t vectors[] = {
    (uint32_t)&_stack_top,          /* 0: SP initial value */
    (uint32_t)Reset_Handler,        /* 1: Reset */
    (uint32_t)NMI_Handler,          /* 2: NMI */
    (uint32_t)HardFault_Handler,    /* 3: Hard Fault */
    (uint32_t)MemManage_Handler,    /* 4: Memory Management */
    (uint32_t)BusFault_Handler,     /* 5: Bus Fault */
    (uint32_t)UsageFault_Handler,   /* 6: Usage Fault */
    0, 0, 0, 0,                     /* 7-10: Reserved */
    (uint32_t)SVC_Handler,          /* 11: SVCall */
    (uint32_t)DebugMon_Handler,     /* 12: Debug Monitor */
    0,                              /* 13: Reserved */
    (uint32_t)PendSV_Handler,       /* 14: PendSV */
    (uint32_t)SysTick_Handler,      /* 15: SysTick */
    /* IRQ handlers 0-47 all default */
};

void Reset_Handler(void)
{
    /* Copy .data from flash to SRAM */
    uint32_t *src = &_etext;
    uint32_t *dst = &_sdata;
    while (dst < &_edata) {
        *dst++ = *src++;
    }

    /* Zero .bss */
    dst = &_sbss;
    while (dst < &_ebss) {
        *dst++ = 0;
    }

    /* Call main */
    main();

    /* If main returns, loop forever */
    while (1) {
        __asm volatile("wfi");
    }
}

void Default_Handler(void)
{
    while (1) {
        __asm volatile("wfi");
    }
}
