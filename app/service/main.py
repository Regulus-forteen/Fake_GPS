"""
前台服务（跨平台）
- Android: 监听 TCP + 注入 GPS + 写 state.json
- 桌面: 监听 TCP + 只写 state.json
"""
import os
import sys
import socket
import threading
import time
import json
import argparse
import traceback

LOG_FILE = None


def _get_state_dir():
    if IS_ANDROID:
        try:
            from jnius import autoclass
            PythonService = autoclass('org.kivy.android.PythonService')
            if PythonService.mService is not None:
                return PythonService.mService.getFilesDir().getAbsolutePath()
        except Exception:
            pass
        return '/data/data/com.example.gpsreceiver/files'
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _init_log():
    global LOG_FILE
    try:
        LOG_FILE = os.path.join(_get_state_dir(), 'service.log')
        with open(LOG_FILE, 'w') as f:
            f.write("")
    except Exception:
        LOG_FILE = None


def _log(msg):
    print(msg)
    if LOG_FILE:
        try:
            with open(LOG_FILE, 'a') as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass


IS_ANDROID = ('ANDROID_ARGUMENT' in os.environ or
              'ANDROID_PRIVATE' in os.environ)

jnius = None
if IS_ANDROID:
    try:
        from jnius import autoclass
        jnius = autoclass
    except Exception as e:
        print(f"[Service] jnius 导入失败: {e}")
        jnius = None


def get_state_file_path():
    if IS_ANDROID:
        try:
            if jnius is not None:
                PythonService = jnius('org.kivy.android.PythonService')
                if PythonService.mService is not None:
                    p = (PythonService.mService.getFilesDir()
                         .getAbsolutePath() + '/state.json')
                    return p
        except Exception:
            pass
        return '/data/data/com.example.gpsreceiver/files/state.json'
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'state.json'
    )


class GpsServer:
    def __init__(self, port=8888):
        self.port = port
        self.running = True
        self.provider_ready = False
        self.count = 0
        self.state_file = get_state_file_path()
        self.foreground_ok = False
        self._last_status = "starting"

        if IS_ANDROID and jnius is not None:
            try:
                PythonService = jnius('org.kivy.android.PythonService')
                Context = jnius('android.content.Context')
                self.service = PythonService.mService
                self.lm = self.service.getSystemService(
                    Context.LOCATION_SERVICE
                )
                self.provider = "gps"
                _log("[Service] Android 服务初始化成功")
            except Exception as e:
                _log(f"[Service] Android 初始化失败: {e}")
                _log(traceback.format_exc())
                self.service = None
                self.lm = None

    def write_state(self, lat=None, lon=None, status="running", extra=None):
        try:
            data = {
                "status": status,
                "count": self.count,
                "timestamp": time.time(),
                "port": self.port,
                "foreground": self.foreground_ok,
            }
            if lat is not None and lon is not None:
                data["lat"] = lat
                data["lon"] = lon
            if extra:
                data.update(extra)
            tmp = self.state_file + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, self.state_file)
        except Exception as e:
            _log(f"[Service] 写状态失败: {e}")

    def start_foreground(self):
        if not IS_ANDROID or jnius is None:
            print("[Service] 桌面模式，跳过前台服务")
            self.foreground_ok = True
            return

        try:
            NotificationBuilder = jnius('android.app.Notification$Builder')
            NotificationChannel = jnius('android.app.NotificationChannel')
            NotificationManager = jnius('android.app.NotificationManager')
            # 关键：嵌套类必须写 Build$VERSION
            BuildVERSION = jnius('android.os.Build$VERSION')
            Context = jnius('android.content.Context')

            sdk_int = BuildVERSION.SDK_INT
            _log(f"[Service] SDK_INT = {sdk_int}")

            if sdk_int >= 26:
                try:
                    channel = NotificationChannel(
                        "gps_receiver", "GPS 接收器",
                        NotificationManager.IMPORTANCE_LOW
                    )
                    nm = self.service.getSystemService(
                        Context.NOTIFICATION_SERVICE)
                    nm.createNotificationChannel(channel)
                except Exception as e:
                    _log(f"[Service] createChannel 失败: {e}")

            notif = None
            try:
                Icon = jnius('android.R$drawable')
                builder = NotificationBuilder(self.service, "gps_receiver")
                builder.setContentTitle("GPS 接收器运行中")
                builder.setContentText(f"监听端口 {self.port}")
                builder.setSmallIcon(Icon.ic_menu_mylocation)
                builder.setOngoing(True)
                notif = builder.build()
            except Exception as e:
                _log(f"[Service] 构造通知失败: {e}")

            if notif is None:
                _log("[Service] 无法构造通知，跳过前台化")
                self.foreground_ok = False
                return

            if sdk_int >= 34:
                try:
                    self.service.startForeground(1001, notif, 8)
                    self.foreground_ok = True
                    _log("[Service] startForeground(3args) OK")
                    return
                except Exception as e:
                    _log(f"[Service] 3args 失败: {e}")

            try:
                self.service.startForeground(1001, notif)
                self.foreground_ok = True
                _log("[Service] startForeground(2args) OK")
            except Exception as e:
                _log(f"[Service] 2args 也失败: {e}")
                self.foreground_ok = False
        except Exception as e:
            _log(f"[Service] start_foreground 顶层失败: {e}")
            _log(traceback.format_exc())
            self.foreground_ok = False

    def setup_provider(self):
        if not IS_ANDROID or jnius is None:
            self.provider_ready = True
            self._last_status = "ready"
            self.write_state(status="ready")
            return
        try:
            # 参数：
            # name, requiresNetwork, requiresSatellite, requiresCell,
            # hasMonetaryCost, supportsAltitude, supportsSpeed,
            # supportsBearing, powerRequirement, accuracy
            # powerRequirement: 1=POWER_LOW, 2=POWER_MEDIUM, 3=POWER_HIGH
            # accuracy: 1=ACCURACY_FINE, 2=ACCURACY_COARSE
            self.lm.addTestProvider(
                self.provider,
                False, False, False, False,
                True, True, True,
                1,    # POWER_LOW
                1     # ACCURACY_FINE
            )
            self.lm.setTestProviderEnabled(self.provider, True)
            self.provider_ready = True
            self._last_status = "provider_ready"
            _log("[Service] Provider 注册成功")
            self.write_state(status="provider_ready")
        except Exception as e:
            _log(f"[Service] addTestProvider 失败: {e}")
            self.provider_ready = False
            self._last_status = "provider_error"
            self.write_state(status="provider_error",
                             extra={"error": str(e)})

    def inject(self, lat, lon):
        if not self.provider_ready:
            return
        self.count += 1
        if IS_ANDROID and jnius is not None:
            try:
                Location = jnius('android.location.Location')
                SystemClock = jnius('android.os.SystemClock')
                loc = Location(self.provider)
                loc.setLatitude(lat)
                loc.setLongitude(lon)
                loc.setAccuracy(3.0)
                loc.setAltitude(50.0)
                loc.setTime(System.currentTimeMillis())
                loc.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos())
                self.lm.setTestProviderLocation(self.provider, loc)
            except Exception as e:
                _log(f"[Service] 注入失败: {e}")
        else:
            if self.count % 10 == 1:
                print(f"[Service] #{self.count}: {lat:.6f}, {lon:.6f}")
        self._last_status = "running"
        self.write_state(lat=lat, lon=lon, status="running")

    def serve(self):
        self.write_state(status="starting")

        # 先监听端口
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.port))
            server.listen(5)
            _log(f"[Service] 监听 0.0.0.0:{self.port}")
            _log(f"[Service] state: {self.state_file}")
            self._last_status = "listening"
            self.write_state(status="listening")

            def _heartbeat():
                while self.running:
                    time.sleep(5)
                    if not self.running:
                        break
                    self.write_state(status=self._last_status)
            threading.Thread(target=_heartbeat, daemon=True).start()
        except Exception as e:
            _log(f"[Service] bind 失败: {e}")
            self.write_state(status="bind_error", extra={"error": str(e)})
            return

        # 再前台服务
        try:
            self.start_foreground()
        except Exception as e:
            _log(f"[Service] start_foreground 外层异常: {e}")

        # 再注册 provider
        try:
            self.setup_provider()
        except Exception as e:
            _log(f"[Service] setup_provider 外层异常: {e}")

        # 处理连接
        try:
            while self.running:
                try:
                    conn, addr = server.accept()
                except KeyboardInterrupt:
                    break
                _log(f"[Service] 客户端连接: {addr}")
                threading.Thread(target=self.handle_client,
                                 args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            _log("\n[Service] Ctrl+C")
        finally:
            server.close()
            self.stop()

    def handle_client(self, conn):
        buffer = ""
        try:
            while self.running:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        lat_str, lon_str = line.split(",")
                        self.inject(float(lat_str), float(lon_str))
                    except ValueError:
                        _log(f"[Service] 格式错误: {line}")
        except Exception as e:
            _log(f"[Service] 客户端异常: {e}")
        finally:
            conn.close()

    def stop(self):
        self.running = False
        self.write_state(status="stopped")
        if IS_ANDROID and jnius is not None:
            try:
                self.lm.removeTestProvider(self.provider)
            except Exception:
                pass


if __name__ == "__main__":
    _init_log()
    _log("=" * 60)
    _log(f"[Service] 启动, IS_ANDROID={IS_ANDROID}")

    try:
        _log("[Service] step1: 解析端口")
        port = 8888
        if IS_ANDROID:
            arg = os.environ.get('PYTHON_SERVICE_ARGUMENT', '')
            _log(f"[Service] PYTHON_SERVICE_ARGUMENT={arg!r}")
            if arg:
                try:
                    port = int(arg)
                except ValueError:
                    pass
        else:
            parser = argparse.ArgumentParser()
            parser.add_argument("--port", type=int, default=8888)
            args = parser.parse_args()
            port = args.port
        _log(f"[Service] 端口={port}")

        _log("[Service] step2: 创建 GpsServer")
        server = GpsServer(port=port)
        _log("[Service] GpsServer 创建完成")

        _log("[Service] step3: 开始 serve()")
        server.serve()
        _log("[Service] serve() 返回（不该到这）")
    except Exception as e:
        _log(f"[Service] 顶层崩溃: {e}")
        _log(traceback.format_exc())