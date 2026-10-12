import socket
import time
import random
import math

# ==== 你之前写的回放引擎放这里 ====
# from route_engine import RoutePlaybackEngine


class PhoneClient:
    def __init__(self, host, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))

    def send(self, lat, lon):
        self.sock.sendall(f"{lat:.7f},{lon:.7f}\n".encode())

    def close(self):
        self.sock.close()


def main():
    # 手机端 Kivy UI 会显示它自己的 IP，填在这里
    client = PhoneClient("192.168.1.100", 8888)

    # 简单演示路线；实际用你的 GPX + RoutePlaybackEngine
    route = [
        (39.9042, 116.4074),
        (39.9080, 116.4120),
        (39.9120, 116.4180),
    ]
    # player = RoutePlaybackEngine(route, 30, loop=True)

    for i in range(500):
        # point = player.next_point()
        # if point is None: break
        t = (i % 100) / 100
        lat = route[0][0] + (route[-1][0] - route[0][0]) * t
        lon = route[0][1] + (route[-1][1] - route[0][1]) * t
        client.send(lat, lon)

        if random.random() < 0.05:
            time.sleep(random.uniform(2, 4))
        else:
            time.sleep(random.uniform(0.6, 1.8))

    client.close()


if __name__ == "__main__":
    main()