/*
 * Minimal "Hello World" firmware for Whoop Maverick
 * Ambiq Apollo4 Blue Plus — ARM Cortex-M4F
 *
 * Demonstrates:
 *   1. GPIO toggle (LED blink via LP5562 I2C — simplified GPIO toggle)
 *   2. UART output "Hello from custom FW"
 *   3. Basic delay loop
 *
 * Apollo4 Blue Plus register addresses:
 *   GPIO:   0x40010000
 *   UART0:  0x4001C000
 *   IOM0:   0x40050000 (I2C/SPI Master 0)
 *
 * WARNING: This is a research/proof-of-concept build.
 *          Do NOT flash on real hardware without full SBL analysis.
 */

#include <stdint.h>

/* Apollo4 UART0 registers */
#define UART0_BASE      0x4001C000
#define UART0_DR        (*(volatile uint32_t *)(UART0_BASE + 0x000))  /* Data register */
#define UART0_FR        (*(volatile uint32_t *)(UART0_BASE + 0x018))  /* Flag register */
#define UART0_IBRD      (*(volatile uint32_t *)(UART0_BASE + 0x024))  /* Integer baud rate */
#define UART0_FBRD      (*(volatile uint32_t *)(UART0_BASE + 0x028))  /* Fractional baud rate */
#define UART0_LCRH      (*(volatile uint32_t *)(UART0_BASE + 0x02C))  /* Line control */
#define UART0_CR        (*(volatile uint32_t *)(UART0_BASE + 0x030))  /* Control */

#define UART_FR_TXFF    (1 << 5)  /* TX FIFO full */

/* Apollo4 GPIO registers */
#define GPIO_BASE       0x40010000
#define GPIO_PADKEY     (*(volatile uint32_t *)(GPIO_BASE + 0x060))
#define GPIO_WTS0       (*(volatile uint32_t *)(GPIO_BASE + 0x088))  /* Write-to-set bank 0 */
#define GPIO_WTC0       (*(volatile uint32_t *)(GPIO_BASE + 0x094))  /* Write-to-clear bank 0 */

/* Apollo4 Clock Gen */
#define CLKGEN_BASE     0x40000000
#define CLKGEN_CALXT    (*(volatile uint32_t *)(CLKGEN_BASE + 0x000))

/* Simple delay */
static void delay(volatile uint32_t count)
{
    while (count--) {
        __asm volatile("nop");
    }
}

/* UART putchar (polling) */
static void uart_putc(char c)
{
    /* Wait until TX FIFO is not full */
    while (UART0_FR & UART_FR_TXFF)
        ;
    UART0_DR = c;
}

/* UART print string */
static void uart_puts(const char *s)
{
    while (*s) {
        if (*s == '\n')
            uart_putc('\r');
        uart_putc(*s++);
    }
}

/* Basic UART0 init (115200 baud, 8N1) */
static void uart_init(void)
{
    /* Assuming 48 MHz HFRC clock (Apollo4 default):
     * Baud = 115200
     * IBRD = 48000000 / (16 * 115200) = 26
     * FBRD = round(0.042 * 64) = 3
     */
    UART0_CR = 0;                  /* Disable UART */
    UART0_IBRD = 26;               /* Integer baud rate divisor */
    UART0_FBRD = 3;                /* Fractional baud rate divisor */
    UART0_LCRH = (3 << 5);        /* 8-bit, no parity, 1 stop, FIFO enabled */
    UART0_CR = (1 << 0) |         /* UART enable */
               (1 << 8) |         /* TX enable */
               (1 << 9);          /* RX enable */
}

/* Toggle a GPIO pin */
static void gpio_toggle(uint32_t pin)
{
    static uint32_t state = 0;
    if (state) {
        GPIO_WTC0 = (1 << pin);
        state = 0;
    } else {
        GPIO_WTS0 = (1 << pin);
        state = 1;
    }
}

int main(void)
{
    /* Initialize UART */
    uart_init();

    /* Print hello message */
    uart_puts("\n\n");
    uart_puts("================================\n");
    uart_puts("  Hello from custom Whoop FW!\n");
    uart_puts("  Ambiq Apollo4 Blue Plus\n");
    uart_puts("  ARM Cortex-M4F @ 96 MHz\n");
    uart_puts("================================\n");
    uart_puts("\n");

    /* Main loop: blink and print */
    uint32_t counter = 0;
    while (1) {
        /* Toggle GPIO (pin 0 as example) */
        gpio_toggle(0);

        /* Print heartbeat every ~1 second */
        if ((counter % 10) == 0) {
            uart_puts("Tick ");
            /* Print counter as decimal */
            char buf[12];
            int i = 0;
            uint32_t n = counter / 10;
            if (n == 0) {
                buf[i++] = '0';
            } else {
                char tmp[12];
                int j = 0;
                while (n > 0) {
                    tmp[j++] = '0' + (n % 10);
                    n /= 10;
                }
                while (j > 0) {
                    buf[i++] = tmp[--j];
                }
            }
            buf[i] = '\0';
            uart_puts(buf);
            uart_puts("\n");
        }

        /* Delay ~100ms (rough estimate at 96 MHz) */
        delay(960000);
        counter++;
    }

    return 0;
}
