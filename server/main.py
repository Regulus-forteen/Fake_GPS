"""
电脑端：路线回放模拟定位发送器
- Web 编辑器里画路线 → 一键回放
- 模拟模式：不连手机，仅网页演示
- 也支持命令行：GPX 文件 / 内置示例路线
- 速度波动 ±20%、高斯漂移 1.5~3m、横向偏移 ±1m
- 随机间隔 0.6~1.8s，5% 概率长停顿 2~4s
零第三方依赖。
"""

import argparse
import math
import random
import socket
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from viewer import (
    start_server, update_state, set_playback_handler,
)


@dataclass
class RoutePoint:
    lat: float
    lon: float


# ============================================================
# 路线回放引擎
# ============================================================
class RoutePlaybackEngine:
    EARTH_RADIUS = 6_371_000.0

    def __init__(self, path, speed_kmh, loop=False):
        self.path = path
        self.speed_kmh = speed_kmh
        self.loop = loop
        self.traveled = 0.0
        self.total_length = self._calc_total()

    def _calc_total(self):
        total = 0.0
        for i in range(len(self.path) - 1):
            total += self._haversine(self.path[i], self.path[i + 1])
        return total

    @staticmethod
    def _haversine(a, b):
        lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
        lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = (math.sin(dlat / 2) ** 2 +
             math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
        return 2 * RoutePlaybackEngine.EARTH_RADIUS * math.asin(math.sqrt(h))

    def next_point(self):
        if len(self.path) < 2:
            return None

        base_step = (self.speed_kmh / 3.6) * 1.0
        step = base_step * (0.8 + random.random() * 0.4)
        self.traveled += step

        if self.traveled >= self.total_length:
            if self.loop:
                self.traveled %= self.total_length
            else:
                return None

        seg_start, seg_end, progress = self._locate(self.traveled)

        base_lat = seg_start.lat + (seg_end.lat - seg_start.lat) * progress
        base_lon = seg_start.lon + (seg_end.lon - seg_start.lon) * progress

        dlat = seg_end.lat - seg_start.lat
        dlon = seg_end.lon - seg_start.lon
        seg_len = math.hypot(dlat, dlon) or 1e-12
        perp_lat = -dlon / seg_len
        perp_lon = dlat / seg_len

        lat_per_m = 1.0 / 111_320.0
        lon_per_m = 1.0 / (111_320.0 * math.cos(math.radians(base_lat)))

        lateral = random.uniform(-1, 1)

        std_dev = 1.5 + random.random() * 1.5
        drift_lat = random.gauss(0, std_dev)
        drift_lon = random.gauss(0, std_dev)

        final_lat = (base_lat + perp_lat * lateral * lat_per_m +
                     drift_lat * lat_per_m)
        final_lon = (base_lon + perp_lon * lateral * lon_per_m +
                     drift_lon * lon_per_m)
        return RoutePoint(final_lat, final_lon)

    def _locate(self, distance):
        remaining = distance
        for i in range(len(self.path) - 1):
            seg_len = self._haversine(self.path[i], self.path[i + 1])
            if remaining <= seg_len:
                progress = remaining / seg_len if seg_len > 0 else 0.0
                return self.path[i], self.path[i + 1], progress
            remaining -= seg_len
        return self.path[-2], self.path[-1], 1.0


# ============================================================
# GPX 解析
# ============================================================
def load_gpx(filename):
    tree = ET.parse(filename)
    root = tree.getroot()
    ns = '{http://www.topografix.com/GPX/1/1}'

    points = []
    for trkpt in root.iter(f'{ns}trkpt'):
        lat = trkpt.get('lat')
        lon = trkpt.get('lon')
        if lat and lon:
            points.append(RoutePoint(float(lat), float(lon)))

    if not points:
        for trkpt in root.iter('trkpt'):
            lat = trkpt.get('lat')
            lon = trkpt.get('lon')
            if lat and lon:
                points.append(RoutePoint(float(lat), float(lon)))
    return points


def sample_route():
    return [
        RoutePoint(39.9042, 116.4074),
        RoutePoint(39.9080, 116.4120),
        RoutePoint(39.9120, 116.4180),
        RoutePoint(39.9160, 116.4230),
        RoutePoint(39.9200, 116.4280),
    ]


# ============================================================
# TCP 客户端
# ============================================================
class PhoneClient:
    def __init__(self, host, port, reconnect=True):
        self.host = host
        self.port = port
        self.sock = None
        self.reconnect = reconnect

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((self.host, self.port))
        self.sock.settimeout(None)
        print(f"[Client] 已连接 {self.host}:{self.port}")

    def send(self, lat, lon):
        if self.sock is None:
            return False
        try:
            self.sock.sendall(f"{lat:.7f},{lon:.7f}\n".encode())
            return True
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print(f"[Client] 发送失败: {e}")
            if self.reconnect:
                self.close()
                try:
                    self.connect()
                except Exception as e2:
                    print(f"[Client] 重连失败: {e2}")
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# ============================================================
# 回放核心
# ============================================================
def playback_loop(route_points, speed, loop, host, port,
                  dry_run, stop_event):
    engine = RoutePlaybackEngine(route_points, speed, loop=loop)

    client = PhoneClient(host, port)
    if not dry_run:
        try:
            client.connect()
            update_state(status="已连接手机", running=True)
        except Exception as e:
            print(f"[Main] 无法连接手机: {e}")
            update_state(status=f"无法连接手机: {e}", running=False)
            return
    else:
        print("[Main] 模拟模式：不连手机")
        update_state(status="模拟模式（本地演示）", running=True)

    count = 0
    try:
        while not stop_event.is_set():
            pt = engine.next_point()
            if pt is None:
                print("[Main] 路线结束")
                update_state(status="已结束", running=False)
                break

            count += 1
            print(f"[{count:05d}] {pt.lat:.7f}, {pt.lon:.7f}")

            update_state(current={"lat": pt.lat, "lon": pt.lon})

            if not dry_run:
                client.send(pt.lat, pt.lon)

            if random.random() < 0.05:
                sleep_time = random.uniform(2, 4)
            else:
                sleep_time = random.uniform(0.6, 1.8)

            slept = 0.0
            while slept < sleep_time and not stop_event.is_set():
                time.sleep(0.1)
                slept += 0.1
    finally:
        client.close()
        print(f"[Main] 共发送 {count} 个坐标")


def make_playback_handler():
    def handler(config, stop_event):
        route_raw = config.get('route', [])
        route_points = [RoutePoint(p['lat'], p['lon']) for p in route_raw]
        speed = float(config.get('speed', 30.0))
        loop = bool(config.get('loop', False))
        host = config.get('host', '192.168.1.100')
        port = int(config.get('port', 8888))
        dry_run = bool(config.get('dry_run', False))

        print(f"[Web] 回放请求: {len(route_points)} 点, {speed} km/h, "
              f"loop={loop}, 模拟={dry_run}, host={host}:{port}")

        update_state(status="准备中")
        playback_loop(route_points, speed, loop, host, port,
                      dry_run, stop_event)

    return handler


# ============================================================
# 命令行入口
# ============================================================
def run_cli(args):
    if args.gpx:
        print(f"[Main] 加载 GPX: {args.gpx}")
        route = load_gpx(args.gpx)
        if len(route) < 2:
            print("[Main] GPX 里没有足够的 trkpt，退出")
            return
    else:
        print("[Main] 使用内置示例路线（北京）")
        route = sample_route()

    print(f"[Main] 路径点: {len(route)} 个")
    print(f"[Main] 速度: {args.speed} km/h, 循环: {args.loop}")

    update_state(
        route=[{"lat": p.lat, "lon": p.lon} for p in route],
        status="命令行模式启动",
    )

    stop_event = threading.Event()
    try:
        playback_loop(route, args.speed, args.loop,
                      args.host, args.port, args.dry_run, stop_event)
    except KeyboardInterrupt:
        print("\n[Main] 用户中断")
        stop_event.set()
        update_state(status="已中断", running=False)


def parse_args():
    p = argparse.ArgumentParser(description="GPS 路线回放发送器")
    p.add_argument("--host", default="192.168.1.100")
    p.add_argument("--port", type=int, default=8888)
    p.add_argument("--speed", type=float, default=30.0)
    p.add_argument("--gpx", default=None)
    p.add_argument("--loop", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--web-port", type=int, default=8000)
    p.add_argument("--cli", action="store_true",
                   help="命令行模式，不启动 Web")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.cli:
        run_cli(args)
    else:
        if args.web_port > 0:
            set_playback_handler(make_playback_handler())
            start_server(args.web_port)
            print(f"\n浏览器打开: http://localhost:{args.web_port}")
            print("画好路线 → 勾选模拟模式 → 开始回放\n")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[Main] 退出")
        else:
            run_cli(args)