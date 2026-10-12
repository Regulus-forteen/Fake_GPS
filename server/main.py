"""
电脑端：路线回放模拟定位发送器
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


DEFAULT_LATERAL_MAX = 1.0
DEFAULT_DRIFT_STD_MIN = 1.5
DEFAULT_DRIFT_STD_MAX = 3.0
DEFAULT_UPDATE_MIN = 0.6
DEFAULT_UPDATE_MAX = 1.8
DEFAULT_PAUSE_PROB = 0.05
DEFAULT_PAUSE_MIN = 2.0
DEFAULT_PAUSE_MAX = 4.0


@dataclass
class RoutePoint:
    lat: float
    lon: float


class RoutePlaybackEngine:
    EARTH_RADIUS = 6_371_000.0

    def __init__(self, path, speed_kmh, loop=False,
                 lateral_max=DEFAULT_LATERAL_MAX,
                 drift_std_min=DEFAULT_DRIFT_STD_MIN,
                 drift_std_max=DEFAULT_DRIFT_STD_MAX):
        self.path = path
        self.speed_kmh = speed_kmh
        self.loop = loop
        self.lateral_max = lateral_max
        self.drift_std_min = drift_std_min
        self.drift_std_max = drift_std_max
        self.traveled = 0.0
        self.total_length = self._calc_total()
        # 平滑误差状态
        self.drift_east = 0.0
        self.drift_north = 0.0
        self.lateral_offset = 0.0

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

        # ---- 平滑误差：随机游走，相邻帧相关 ----
        smooth = 0.15
        std_dev = random.uniform(self.drift_std_min, self.drift_std_max)

        self.drift_east  += random.gauss(0, std_dev * smooth)
        self.drift_north += random.gauss(0, std_dev * smooth)
        max_drift = 3 * std_dev if std_dev > 0 else 0
        if max_drift > 0:
            self.drift_east  = max(-max_drift, min(max_drift, self.drift_east))
            self.drift_north = max(-max_drift, min(max_drift, self.drift_north))

        if self.lateral_max > 0:
            self.lateral_offset += random.gauss(0, self.lateral_max * smooth)
            self.lateral_offset = max(-self.lateral_max,
                                      min(self.lateral_max, self.lateral_offset))
        else:
            self.lateral_offset = 0.0

        lateral = self.lateral_offset
        drift_lat = self.drift_north / 111_320.0
        drift_lon = self.drift_east / (111_320.0 *
                                       math.cos(math.radians(base_lat)))

        final_lat = base_lat + perp_lat * lateral * lat_per_m + drift_lat
        final_lon = base_lon + perp_lon * lateral * lon_per_m + drift_lon
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


def playback_loop(route_points, speed, loop, host, port,
                  dry_run, stop_event,
                  lateral_max=DEFAULT_LATERAL_MAX,
                  drift_std_min=DEFAULT_DRIFT_STD_MIN,
                  drift_std_max=DEFAULT_DRIFT_STD_MAX,
                  update_min=DEFAULT_UPDATE_MIN,
                  update_max=DEFAULT_UPDATE_MAX,
                  pause_prob=DEFAULT_PAUSE_PROB,
                  pause_min=DEFAULT_PAUSE_MIN,
                  pause_max=DEFAULT_PAUSE_MAX):
    engine = RoutePlaybackEngine(
        route_points, speed, loop=loop,
        lateral_max=lateral_max,
        drift_std_min=drift_std_min,
        drift_std_max=drift_std_max,
    )

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

            if random.random() < pause_prob:
                sleep_time = random.uniform(pause_min, pause_max)
            else:
                sleep_time = random.uniform(update_min, update_max)

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

        lateral_max = float(config.get('lateral_max', DEFAULT_LATERAL_MAX))
        drift_std_min = float(config.get('drift_std_min', DEFAULT_DRIFT_STD_MIN))
        drift_std_max = float(config.get('drift_std_max', DEFAULT_DRIFT_STD_MAX))
        update_min = float(config.get('update_min', DEFAULT_UPDATE_MIN))
        update_max = float(config.get('update_max', DEFAULT_UPDATE_MAX))
        pause_prob = float(config.get('pause_prob', DEFAULT_PAUSE_PROB))
        pause_min = float(config.get('pause_min', DEFAULT_PAUSE_MIN))
        pause_max = float(config.get('pause_max', DEFAULT_PAUSE_MAX))

        print(f"[Web] 回放请求: {len(route_points)} 点, {speed} km/h, "
              f"loop={loop}, 模拟={dry_run}, host={host}:{port}, "
              f"横向±{lateral_max}m, 漂移σ∈[{drift_std_min},{drift_std_max}]m, "
              f"间隔∈[{update_min},{update_max}]s, 停顿{ pause_prob*100:.0f}%")

        update_state(status="准备中")
        playback_loop(route_points, speed, loop, host, port,
                      dry_run, stop_event,
                      lateral_max=lateral_max,
                      drift_std_min=drift_std_min,
                      drift_std_max=drift_std_max,
                      update_min=update_min,
                      update_max=update_max,
                      pause_prob=pause_prob,
                      pause_min=pause_min,
                      pause_max=pause_max)
    return handler


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
                      args.host, args.port, args.dry_run, stop_event,
                      lateral_max=args.lateral_max,
                      drift_std_min=args.drift_std_min,
                      drift_std_max=args.drift_std_max,
                      update_min=args.update_min,
                      update_max=args.update_max,
                      pause_prob=args.pause_prob,
                      pause_min=args.pause_min,
                      pause_max=args.pause_max)
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
    p.add_argument("--cli", action="store_true")
    p.add_argument("--lateral-max", type=float, default=DEFAULT_LATERAL_MAX)
    p.add_argument("--drift-std-min", type=float, default=DEFAULT_DRIFT_STD_MIN)
    p.add_argument("--drift-std-max", type=float, default=DEFAULT_DRIFT_STD_MAX)
    p.add_argument("--update-min", type=float, default=DEFAULT_UPDATE_MIN)
    p.add_argument("--update-max", type=float, default=DEFAULT_UPDATE_MAX)
    p.add_argument("--pause-prob", type=float, default=DEFAULT_PAUSE_PROB)
    p.add_argument("--pause-min", type=float, default=DEFAULT_PAUSE_MIN)
    p.add_argument("--pause-max", type=float, default=DEFAULT_PAUSE_MAX)
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