[app]
title = Fake GPS
package.name = fakegps
package.domain = com.regulus14

version = 0.1

# 图标与启动图（相对 buildozer.spec 所在目录）
icon.filename = icon.png
presplash.filename = presplash.png

# 发布签名类型
android.release_artifact = apk
android.debug_artifact = apk

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf

requirements = python3,kivy,pyjnius,kivy_garden.mapview

android.permissions = ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION, FOREGROUND_SERVICE, FOREGROUND_SERVICE_LOCATION, POST_NOTIFICATIONS, INTERNET

services = Receiver:service/main.py:foreground:foregroundServiceType=location

android.minapi = 26
android.api = 34
android.targetapi = 34
android.archs = arm64-v8a, armeabi-v7a
orientation = portrait
android.logcat_filters = *:S python:D
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1