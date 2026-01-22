# Makefile for Whoop Maverick custom firmware (Ambiq Apollo4 Blue Plus)
#
# Prerequisites: arm-none-eabi-gcc (ARM cross-compiler)
# Build: make
# Clean: make clean
#
# WARNING: Do NOT flash the output on real hardware without
#          full SBL (Secure Boot Loader) analysis.

# Toolchain
CC      = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
OBJDUMP = arm-none-eabi-objdump
SIZE    = arm-none-eabi-size

# Target
TARGET  = whoop_custom

# Flags
CFLAGS  = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16
CFLAGS += -O2 -g -Wall -Wextra -Werror
CFLAGS += -ffunction-sections -fdata-sections
CFLAGS += -nostdlib -ffreestanding

LDFLAGS = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16
LDFLAGS += -T linker.ld -nostdlib
LDFLAGS += -Wl,--gc-sections
LDFLAGS += -Wl,-Map=$(TARGET).map

# Sources
SRCS    = startup.c main.c
OBJS    = $(SRCS:.c=.o)

# Rules
all: $(TARGET).bin $(TARGET).dis
	@echo ""
	@echo "Build complete:"
	@$(SIZE) $(TARGET).elf
	@echo ""
	@echo "Output files:"
	@ls -la $(TARGET).bin $(TARGET).elf $(TARGET).dis $(TARGET).map
	@echo ""
	@echo "To create .zbin:"
	@echo "  python3 ../tools/zbin_builder.py --build $(TARGET).bin -o $(TARGET).zbin"
	@echo ""
	@echo "WARNING: Do NOT flash without full SBL analysis!"

$(TARGET).elf: $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

$(TARGET).dis: $(TARGET).elf
	$(OBJDUMP) -d -S $< > $@

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET).elf $(TARGET).bin $(TARGET).map $(TARGET).dis $(TARGET).zbin

.PHONY: all clean
