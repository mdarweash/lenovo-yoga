#!/bin/bash
# Test script: read orientation sensor once, fast

/usr/bin/python3 -c "
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None,
    'net.hadess.SensorProxy', '/net/hadess/SensorProxy',
    'net.hadess.SensorProxy', None)
proxy.call_sync('ClaimAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
result = proxy.call_sync('org.freedesktop.DBus.Properties.Get',
    GLib.Variant('(ss)', ('net.hadess.SensorProxy', 'AccelerometerOrientation')),
    Gio.DBusCallFlags.NONE, -1, None)
print(result.unpack()[0])
proxy.call_sync('ReleaseAccelerometer', None, Gio.DBusCallFlags.NONE, -1, None)
"
