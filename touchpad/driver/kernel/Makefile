obj-m += hid-yb9-touchpad.o

KDIR := /lib/modules/$(shell uname -r)/build

all:
	make -C $(KDIR) M=$(PWD) modules

clean:
	make -C $(KDIR) M=$(PWD) clean

install:
	make -C $(KDIR) M=$(PWD) modules_install
	depmod -a

load:
	-modprobe -r hid-yb9-touchpad
	insmod hid-yb9-touchpad.ko

unload:
	rmmod hid-yb9-touchpad

.PHONY: all clean install load unload
