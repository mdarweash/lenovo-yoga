sudo systemctl stop sddm   # or whatever display manager
sudo rmmod i915
sudo modprobe i915 enable_dpcd_backlight=2
sudo systemctl start sddm
