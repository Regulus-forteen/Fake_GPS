[app]
title = GPS Receiver
package.name = gpsreceiver
package.domain = com.example

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.1

requirements = python3==3.11.5,hostpython3==3.11.5,kivy,pyjnius

android.permissions = ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION, ACCESS_MOCK_LOCATION, FOREGROUND_SERVICE, FOREGROUND_SERVICE_LOCATION, POST_NOTIFICATIONS

services = Receiver:service/main.py:foreground

# p4a.hook = for-build/p4a/hook.py

android.minapi = 26
android.api = 34
android.targetapi = 34
android.archs = arm64-v8a, armeabi-v7a
android.logcat_filters = *:S python:D
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1