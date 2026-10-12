"""
前台服务：TCP 监听 + GPS 注入。
电脑端发 "lat,lon\n"，本服务逐行解析并注入。
"""
import os
import sys
import socket
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class GpsServer:
    def __init__(self, port=8888):
        self.port = port
        self.running = True
        self.service = PythonService.mService   # 关键：用服务自身作为 Context
        self.lm = self.service.getSystemService(Context.LOCATION_SERVICE)
        self.provider = "gps"
        self.provider_ready = False

    # ---- 前台服务通知 ----
    def start_foreground(self):
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

        if Build.VERSION.SDK_INT >= 34:
            # FOREGROUND_SERVICE_TYPE_LOCATION = 8
            self.service.startForeground(1001, builder.build(), 8)
        else:
            self.service.startForeground(1001, builder.build())

    # ---- 测试 Provider ----
    def setup_provider(self):
        try:
            self.lm.addTestProvider(
                self.provider,
                False, False, False, False,
                True, True, True, 0, 5
            )
            self.lm.setTestProviderEnabled(self.provider, True)
            self.provider_ready = True
            print("[Service] 测试 Provider 注册成功")
        except Exception as e:
            print(f"[Service] addTestProvider 失败: {e}")
            self.provider_ready = False

    def inject(self, lat, lon):
        if not self.provider_ready:
            return
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

    # ---- TCP 服务器 ----
    def serve(self):
        self.start_foreground()
        self.setup_provider()

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", self.port))
        server.listen(5)
        server.settimeout(1.0)
        print(f"[Service] 监听 0.0.0.0:{self.port}")

        while self.running:
            try:
                conn, addr = server.accept()
                print(f"[Service] 客户端连接: {addr}")
                threading.Thread(
                    target=self.handle_client, args=(conn,), daemon=True
                ).start()
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[Service] accept 异常: {e}")
                break

        server.close()

    def handle_client(self, conn):
        """逐行读取 'lat,lon' 并注入"""
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
                        lat, lon = float(lat_str), float(lon_str)
                        self.inject(lat, lon)
                    except ValueError:
                        print(f"[Service] 格式错误: {line}")
        except Exception as e:
            print(f"[Service] 客户端异常: {e}")
        finally:
            conn.close()
            print("[Service] 客户端断开")

    def stop(self):
        self.running = False
        try:
            self.lm.removeTestProvider(self.provider)
        except Exception:
            pass


if __name__ == "__main__":
    port = 8888
    try:
        intent = PythonService.mService.getIntent()
        p = intent.getIntExtra("port", 8888)
        if p > 0:
            port = p
    except Exception:
        pass

    server = GpsServer(port=port)
    try:
        server.serve()
    finally:
        server.stop()