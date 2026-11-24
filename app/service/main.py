"""
前台服务（跨平台）
- 监听 TCP + 注入 GPS/Network + 每100ms高频写
- 定期检查 mock provider 是否被系统移除，被移除立即重建
- 持有 PARTIAL_WAKE_LOCK 防止后台被冻结
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
        return '/data/data/com.regulus14.fakegps/files'
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
        return '/data/data/com.regulus14.fakegps/files/state.json'
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'state.json'
    )


GPS_PROVIDER = "gps"
NETWORK_PROVIDER = "network"

POWER_USAGE_LOW = 1
POWER_USAGE_MEDIUM = 2
POWER_USAGE_HIGH = 3
ACCURACY_FINE = 1
ACCURACY_COARSE = 2


class GpsServer:
    def __init__(self, port=8888):
        self.port = port
        self.running = True
        self.provider_ready = False
        self.count = 0
        self.state_file = get_state_file_path()
        self.foreground_ok = False
        self._last_status = "starting"

        self.cur_lat = 39.9042
        self.cur_lon = 116.4074
        self.cur_alt = 55.0
        self.cur_bearing = 0.0
        self.cur_speed = 1.2

        self._inject_thread = None
        self._guard_thread = None
        self._wake_lock = None

        # 记录重建次数（用于日志）
        self._rebuild_count = 0

        if IS_ANDROID and jnius is not None:
            try:
                PythonService = jnius('org.kivy.android.PythonService')
                Context = jnius('android.content.Context')
                self.service = PythonService.mService
                self.lm = self.service.getSystemService(
                    Context.LOCATION_SERVICE
                )
                _log("[Service] Android 服务初始化成功")
            except Exception as e:
                _log(f"[Service] Android 初始化失败: {e}")
                _log(traceback.format_exc())
                self.service = None
                self.lm = None

    # ========================================================
    # WakeLock
    # ========================================================
    def acquire_wakelock(self):
        if not IS_ANDROID or jnius is None:
            return
        try:
            Context = jnius('android.content.Context')
            PowerManager = jnius('android.os.PowerManager')
            pm = self.service.getSystemService(Context.POWER_SERVICE)
            self._wake_lock = pm.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "fakegps::InjectLoop"
            )
            self._wake_lock.acquire()
            _log("[Service] WakeLock 已获取")
        except Exception as e:
            _log(f"[Service] WakeLock 获取失败: {e}")
            self._wake_lock = None

    def release_wakelock(self):
        if self._wake_lock is not None:
            try:
                self._wake_lock.release()
                _log("[Service] WakeLock 已释放")
            except Exception:
                pass
            self._wake_lock = None

    # ========================================================
    def write_state(self, lat=None, lon=None, status="running", extra=None):
        try:
            data = {
                "status": status,
                "count": self.count,
                "timestamp": time.time(),
                "port": self.port,
                "foreground": self.foreground_ok,
                "rebuild": self._rebuild_count,
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

    # ========================================================
    # Provider 注册 / 移除
    # ========================================================
    def _is_mock_present(self, name):
        """检查 mock provider 是否还在（通过尝试写入）"""
        try:
            Location = jnius('android.location.Location')
            loc = Location(name)
            loc.setLatitude(self.cur_lat)
            loc.setLongitude(self.cur_lon)
            loc.setTime(int(time.time() * 1000))
            self.lm.setTestProviderLocation(name, loc)
            return True
        except Exception:
            return False

    def _remove_provider(self, name):
        try:
            if self.lm.isProviderEnabled(name):
                self.lm.setTestProviderEnabled(name, False)
                self.lm.removeTestProvider(name)
        except Exception:
            pass

    def _add_gps_provider(self):
        try:
            BuildVERSION = jnius('android.os.Build$VERSION')
            sdk_int = BuildVERSION.SDK_INT
            if sdk_int >= 31:
                self.lm.addTestProvider(
                    GPS_PROVIDER,
                    False, True, False, False,
                    True, True, True,
                    POWER_USAGE_HIGH,
                    ACCURACY_FINE
                )
            else:
                Criteria = jnius('android.location.Criteria')
                self.lm.addTestProvider(
                    GPS_PROVIDER,
                    False, True, False, False,
                    True, True, True,
                    Criteria.POWER_HIGH,
                    Criteria.ACCURACY_FINE
                )
            if not self.lm.isProviderEnabled(GPS_PROVIDER):
                self.lm.setTestProviderEnabled(GPS_PROVIDER, True)
            return True
        except Exception:
            return False

    def _add_network_provider(self):
        try:
            BuildVERSION = jnius('android.os.Build$VERSION')
            sdk_int = BuildVERSION.SDK_INT
            if sdk_int >= 31:
                self.lm.addTestProvider(
                    NETWORK_PROVIDER,
                    True, False, True, True,
                    True, True, True,
                    POWER_USAGE_LOW,
                    ACCURACY_COARSE
                )
            else:
                Criteria = jnius('android.location.Criteria')
                self.lm.addTestProvider(
                    NETWORK_PROVIDER,
                    True, False, True, True,
                    True, True, True,
                    Criteria.POWER_LOW,
                    Criteria.ACCURACY_COARSE
                )
            if not self.lm.isProviderEnabled(NETWORK_PROVIDER):
                self.lm.setTestProviderEnabled(NETWORK_PROVIDER, True)
            return True
        except Exception:
            return False

    def setup_provider(self):
        if not IS_ANDROID or jnius is None:
            self.provider_ready = True
            self._last_status = "ready"
            self.write_state(status="ready")
            return

        self._remove_provider(GPS_PROVIDER)
        self._remove_provider(NETWORK_PROVIDER)

        ok1 = self._add_gps_provider()
        ok2 = self._add_network_provider()

        if ok1 or ok2:
            self.provider_ready = True
            self._last_status = "provider_ready"
            self.write_state(status="provider_ready")
            try:
                self._write_location_gps()
                self._write_location_network()
            except Exception:
                pass
            _log(f"[Service] provider 就绪 gps={ok1} network={ok2}")
        else:
            self.provider_ready = False
            self._last_status = "provider_error"
            self.write_state(status="provider_error",
                             extra={"error": "all providers failed"})

    # ========================================================
    # 单次写入
    # ========================================================
    def _write_location_gps(self):
        try:
            Location = jnius('android.location.Location')
            SystemClock = jnius('android.os.SystemClock')
            Bundle = jnius('android.os.Bundle')

            loc = Location(GPS_PROVIDER)
            loc.setAccuracy(3.0)
            loc.setAltitude(self.cur_alt)
            loc.setBearing(self.cur_bearing)
            loc.setLatitude(self.cur_lat)
            loc.setLongitude(self.cur_lon)
            loc.setTime(int(time.time() * 1000))
            loc.setSpeed(float(self.cur_speed))
            loc.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos())

            bundle = Bundle()
            bundle.putInt("satellites", 7)
            try:
                bundle.putFloat("maxCn0", 38.0)
                bundle.putFloat("meanCn0", 28.0)
            except Exception:
                pass
            loc.setExtras(bundle)

            self.lm.setTestProviderLocation(GPS_PROVIDER, loc)
            return True
        except Exception:
            return False

    def _write_location_network(self):
        try:
            Location = jnius('android.location.Location')
            SystemClock = jnius('android.os.SystemClock')

            loc = Location(NETWORK_PROVIDER)
            loc.setAccuracy(50.0)
            loc.setAltitude(self.cur_alt)
            loc.setBearing(self.cur_bearing)
            loc.setLatitude(self.cur_lat)
            loc.setLongitude(self.cur_lon)
            loc.setTime(int(time.time() * 1000))
            loc.setSpeed(float(self.cur_speed))
            loc.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos())

            self.lm.setTestProviderLocation(NETWORK_PROVIDER, loc)
            return True
        except Exception:
            return False

    # ========================================================
    # 高频写入线程
    # ========================================================
    def _inject_loop(self):
        while self.running:
            if self.provider_ready:
                self._write_location_gps()
                self._write_location_network()
            time.sleep(0.1)

    def start_inject_loop(self):
        if self._inject_thread is None:
            self._inject_thread = threading.Thread(
                target=self._inject_loop, daemon=True
            )
            self._inject_thread.start()
            _log("[Service] 高频注入线程已启动")

    # ========================================================
    # 守 provider 线程：每 3 秒检查 mock 是否被系统移除
    # ========================================================
    def _guard_loop(self):
        while self.running:
            time.sleep(3)
            if not self.running:
                break
            if not IS_ANDROID or jnius is None:
                continue
            try:
                gps_ok = self._is_mock_present(GPS_PROVIDER)
                net_ok = self._is_mock_present(NETWORK_PROVIDER)
                if not (gps_ok and net_ok):
                    self._rebuild_count += 1
                    _log(f"[Guard] 检测到 mock 丢失 "
                         f"(gps={gps_ok}, net={net_ok})，重建第 "
                         f"{self._rebuild_count} 次")
                    self.setup_provider()
            except Exception as e:
                _log(f"[Guard] 检查失败: {e}")

    def start_guard_loop(self):
        if self._guard_thread is None:
            self._guard_thread = threading.Thread(
                target=self._guard_loop, daemon=True
            )
            self._guard_thread.start()
            _log("[Service] 守 provider 线程已启动（每 3 秒检查）")

    # ========================================================
    def inject(self, lat, lon):
        if not self.provider_ready:
            return
        self.count += 1
        self.cur_lat = lat
        self.cur_lon = lon
        self._last_status = "running"
        self.write_state(lat=lat, lon=lon, status="running")

    def serve(self):
        self.write_state(status="starting")
        self.acquire_wakelock()

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

        try:
            self.start_foreground()
        except Exception as e:
            _log(f"[Service] start_foreground 外层异常: {e}")

        try:
            self.setup_provider()
        except Exception as e:
            _log(f"[Service] setup_provider 外层异常: {e}")

        try:
            self.start_inject_loop()
        except Exception as e:
            _log(f"[Service] start_inject_loop 外层异常: {e}")

        try:
            self.start_guard_loop()
        except Exception as e:
            _log(f"[Service] start_guard_loop 外层异常: {e}")

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
                        pass
        except Exception as e:
            _log(f"[Service] 客户端异常: {e}")
        finally:
            conn.close()

    def stop(self):
        self.running = False
        self.write_state(status="stopped")
        if IS_ANDROID and jnius is not None:
            self._remove_provider(GPS_PROVIDER)
            self._remove_provider(NETWORK_PROVIDER)
        self.release_wakelock()


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