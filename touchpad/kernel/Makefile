obj-m += ingenic_mcu.o
ingenic_mcu-y := ingenic_main.o ingenic_oskp.o ingenic_touchpad.o ingenic_sysfs.o

KDIR ?= /lib/modules/$(shell uname -r)/build
M_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))

all:
	$(MAKE) -C $(KDIR) M=$(M_DIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(M_DIR) clean

install:
	$(MAKE) -C $(KDIR) M=$(M_DIR) modules_install
	depmod -a

help:
	@echo "Targets:"
	@echo "  all     - Build the kernel module"
	@echo "  clean   - Remove build artifacts"
	@echo "  install - Install module to current kernel"
