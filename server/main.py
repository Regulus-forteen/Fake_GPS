"""
电脑端：路线回放模拟定位发送器
- 路线模式：沿路径移动
- 定点模式：中心点附近随机漂移
- 每次注入上传实际坐标给网页
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

        smooth = 0.15
        std_dev = random.uniform(self.drift_std_min, self.drift_std_max)
        self.drift_east  += random.gauss(0, std_dev * smooth)
        self.drift_north += random.gauss(0, std_dev * smooth)
        max_drift = 3 * std_dev if std_dev > 0 else 0
        if max_drift > 0:
            self.drift_east = max(-max_drift, min(max_drift, self.drift_east))
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


# ============================================================
# 实际点上传（限流：每 3 个点更新一次 STATE）
# ============================================================
_actual_buffer = []
_actual_lock = threading.Lock()
_actual_counter = [0]


def push_actual(lat, lon):
    with _actual_lock:
        _actual_buffer.append({"lat": lat, "lon": lon})
        if len(_actual_buffer) > 2000:
            del _actual_buffer[:-2000]
        _actual_counter[0] += 1
        if _actual_counter[0] % 3 == 0:
            update_state(actual=list(_actual_buffer))


# ============================================================
# 路线回放
# ============================================================
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
            push_actual(pt.lat, pt.lon)

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


# ============================================================
# 定点漂移
# ============================================================
def home_loop(center_lat, center_lon, host, port,
              dry_run, stop_event,
              drift_std=1.0, update_min=0.6, update_max=1.8):
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
        update_state(status="定点模拟（本地演示）", running=True)

    count = 0
    dx = 0.0
    dy = 0.0
    lat_per_m = 1.0 / 111_320.0
    lon_per_m = 1.0 / (111_320.0 * math.cos(math.radians(center_lat)))

    try:
        while not stop_event.is_set():
            dx += random.gauss(0, drift_std * 0.15)
            dy += random.gauss(0, drift_std * 0.15)
            max_drift = 3 * drift_std
            dx = max(-max_drift, min(max_drift, dx))
            dy = max(-max_drift, min(max_drift, dy))

            lat = center_lat + dy * lat_per_m
            lon = center_lon + dx * lon_per_m

            count += 1
            print(f"[Home {count:05d}] {lat:.7f}, {lon:.7f}")
            update_state(current={"lat": lat, "lon": lon})
            push_actual(lat, lon)

            if not dry_run:
                client.send(lat, lon)

            sleep_time = random.uniform(update_min, update_max)
            slept = 0.0
            while slept < sleep_time and not stop_event.is_set():
                time.sleep(0.1)
                slept += 0.1
    finally:
        client.close()
        print(f"[Home] 共发送 {count} 个坐标")


# ============================================================
# 回放调度
# ============================================================
def make_playback_handler():
    def handler(config, stop_event):
        mode = config.get('mode', 'route')
        host = config.get('host', '192.168.1.100')
        port = int(config.get('port', 8888))
        dry_run = bool(config.get('dry_run', False))
        update_min = float(config.get('update_min', DEFAULT_UPDATE_MIN))
        update_max = float(config.get('update_max', DEFAULT_UPDATE_MAX))
        lateral_max = float(config.get('lateral_max', DEFAULT_LATERAL_MAX))
        drift_std_min = float(config.get('drift_std_min', DEFAULT_DRIFT_STD_MIN))
        drift_std_max = float(config.get('drift_std_max', DEFAULT_DRIFT_STD_MAX))

        # 重置 actual 缓冲
        with _actual_lock:
            _actual_buffer.clear()
            _actual_counter[0] = 0
        update_state(actual=[])

        if mode == 'home':
            home = config.get('home', {})
            center_lat = float(home['lat'])
            center_lon = float(home['lon'])
            drift_std = (drift_std_min + drift_std_max) / 2
            if drift_std <= 0:
                drift_std = 1.0
            print(f"[Web] 定点: ({center_lat:.6f},{center_lon:.6f}), "
                  f"σ={drift_std}m")
            home_loop(center_lat, center_lon, host, port,
                      dry_run, stop_event,
                      drift_std=drift_std,
                      update_min=update_min, update_max=update_max)
            return

        # route 模式
        route_raw = config.get('route', [])
        route_points = [RoutePoint(p['lat'], p['lon']) for p in route_raw]
        speed = float(config.get('speed', 30.0))
        loop = bool(config.get('loop', False))
        pause_prob = float(config.get('pause_prob', DEFAULT_PAUSE_PROB))
        pause_min = float(config.get('pause_min', DEFAULT_PAUSE_MIN))
        pause_max = float(config.get('pause_max', DEFAULT_PAUSE_MAX))

        print(f"[Web] 路线: {len(route_points)} 点, {speed} km/h, loop={loop}")
        playback_loop(route_points, speed, loop, host, port,
                      dry_run, stop_event,
                      lateral_max=lateral_max,
                      drift_std_min=drift_std_min,
                      drift_std_max=drift_std_max,
                      update_min=update_min, update_max=update_max,
                      pause_prob=pause_prob,
                      pause_min=pause_min, pause_max=pause_max)
    return handler


# ============================================================
# 命令行入口
# ============================================================
def run_cli(args):
    if args.gpx:
        route = load_gpx(args.gpx)
        if len(route) < 2:
            print("[Main] GPX 里没有足够的 trkpt，退出")
            return
    else:
        route = sample_route()
    update_state(
        route=[{"lat": p.lat, "lon": p.lon} for p in route],
        status="命令行模式启动",
    )
    stop_event = threading.Event()
    try:
        playback_loop(route, args.speed, args.loop,
                      args.host, args.port, args.dry_run, stop_event)
    except KeyboardInterrupt:
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
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.cli:
        run_cli(args)
    else:
        if args.web_port > 0:
            set_playback_handler(make_playback_handler())
            start_server(args.web_port)
            print(f"\n浏览器打开: http://localhost:{args.web_port}\n")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[Main] 退出")
        else:
            run_cli(args)