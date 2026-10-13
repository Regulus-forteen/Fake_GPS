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

IS_ANDROID = ('ANDROID_ARGUMENT' in os.environ or
              'ANDROID_PRIVATE' in os.environ)

if IS_ANDROID:
    from jnius import autoclass
    Context = autoclass('android.content.Context')
    LocationManager = autoclass('android.location.LocationManager')
    Location = autoclass('android.location.Location')
    SystemClock = autoclass('android.os.SystemClock')
    NotificationBuilder = autoclass('android.app.Notification$Builder')
    NotificationChannel = autoclass('android.app.NotificationChannel')
    NotificationManager = autoclass('android.app.NotificationManager')
    Build = autoclass('android.os.Build')
    PythonService = autoclass('org.kivy.android.PythonService')


def get_state_file_path():
    if IS_ANDROID:
        try:
            files_dir = PythonService.mService.getFilesDir().getAbsolutePath()
            return os.path.join(files_dir, 'state.json')
        except Exception as e:
            print(f"[Path] {e}")
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

        if IS_ANDROID:
            self.service = PythonService.mService
            self.lm = self.service.getSystemService(Context.LOCATION_SERVICE)
            self.provider = "gps"

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
            print(f"[Service] 写状态失败: {e}")

    def start_foreground(self):
        if not IS_ANDROID:
            print("[Service] 桌面模式，跳过前台服务")
            self.foreground_ok = True
            return

        try:
            if Build.VERSION.SDK_INT >= 26:
                channel = NotificationChannel(
                    "gps_receiver", "GPS 接收器",
                    NotificationManager.IMPORTANCE_LOW
                )
                nm = self.service.getSystemService(Context.NOTIFICATION_SERVICE)
                nm.createNotificationChannel(channel)

            Icon = autoclass('android.R$drawable')
            builder = NotificationBuilder(self.service, "gps_receiver")
            builder.setContentTitle("GPS 接收器运行中")
            builder.setContentText(f"监听端口 {self.port}")
            builder.setSmallIcon(Icon.ic_menu_mylocation)
            builder.setOngoing(True)
            notif = builder.build()

            if Build.VERSION.SDK_INT >= 34:
                try:
                    self.service.startForeground(1001, notif, 8)
                    self.foreground_ok = True
                    print("[Service] startForeground(3args) OK")
                except Exception as e:
                    print(f"[Service] startForeground(3args) 失败: {e}")
                    try:
                        self.service.startForeground(1001, notif)
                        self.foreground_ok = True
                        print("[Service] startForeground(2args) OK")
                    except Exception as e2:
                        print(f"[Service] startForeground(2args) 也失败: {e2}")
            else:
                self.service.startForeground(1001, notif)
                self.foreground_ok = True
                print("[Service] startForeground OK")
        except Exception as e:
            print(f"[Service] start_foreground 整体失败: {e}")

    def setup_provider(self):
        if not IS_ANDROID:
            self.provider_ready = True
            self.write_state(status="ready")
            return
        try:
            self.lm.addTestProvider(
                self.provider,
                False, False, False, False,
                True, True, True, 0, 5
            )
            self.lm.setTestProviderEnabled(self.provider, True)
            self.provider_ready = True
            print("[Service] Provider 注册成功")
            self.write_state(status="provider_ready")
        except Exception as e:
            print(f"[Service] addTestProvider 失败: {e}")
            self.provider_ready = False
            self.write_state(status="provider_error", extra={"error": str(e)})

    def inject(self, lat, lon):
        if not self.provider_ready:
            return
        self.count += 1
        if IS_ANDROID:
            try:
                loc = Location(self.provider)
                loc.setLatitude(lat)
                loc.setLongitude(lon)
                loc.setAccuracy(3.0)
                loc.setAltitude(50.0)
                loc.setTime(System.currentTimeMillis())
                loc.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos())
                self.lm.setTestProviderLocation(self.provider, loc)
            except Exception as e:
                print(f"[Service] 注入失败: {e}")
        else:
            if self.count % 10 == 1:
                print(f"[Service] #{self.count}: {lat:.6f}, {lon:.6f}")
        self.write_state(lat=lat, lon=lon, status="running")

    def serve(self):
        self.write_state(status="starting")
        self.start_foreground()
        self.setup_provider()

        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.port))
            server.listen(5)
            print(f"[Service] 监听 0.0.0.0:{self.port}")
            print(f"[Service] state: {self.state_file}")
            self.write_state(status="listening")
        except Exception as e:
            print(f"[Service] bind 失败: {e}")
            self.write_state(status="bind_error", extra={"error": str(e)})
            return

        try:
            while self.running:
                try:
                    conn, addr = server.accept()
                except KeyboardInterrupt:
                    break
                print(f"[Service] 客户端连接: {addr}")
                threading.Thread(target=self.handle_client,
                                 args=(conn,), daemon=True).start()
        except KeyboardInterrupt:
            print("\n[Service] Ctrl+C")
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
                        print(f"[Service] 格式错误: {line}")
        except Exception as e:
            print(f"[Service] 客户端异常: {e}")
        finally:
            conn.close()

    def stop(self):
        self.running = False
        self.write_state(status="stopped")
        if IS_ANDROID:
            try:
                self.lm.removeTestProvider(self.provider)
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()

    port = args.port
    if IS_ANDROID:
        try:
            intent = PythonService.mService.getIntent()
            p = intent.getIntExtra("port", 8888)
            if p > 0:
                port = p
        except Exception:
            pass

    server = GpsServer(port=port)
    server.serve()