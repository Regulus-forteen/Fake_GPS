# ========== 必须在所有 Kivy import 之前 ==========
from kivy.config import Config
Config.set('graphics', 'orientation', 'portrait')
Config.set('graphics', 'width', '420')
Config.set('graphics', 'height', '820')
Config.set('graphics', 'resizable', True)

import os
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
except ImportError:
    pass
# ================================================

from kivy.app import App
from kivy.core.text import LabelBase
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line, Ellipse
from kivy_garden.mapview import MapView, MapSource, MapLayer
import socket
import sys
import json
import math
import subprocess
import signal

IS_ANDROID = ('ANDROID_ARGUMENT' in os.environ or
              'ANDROID_PRIVATE' in os.environ)

if IS_ANDROID:
    try:
        from jnius import autoclass
        from android import mActivity
    except Exception as e:
        print(f"[WARN] Android 模块导入失败: {e}")
        IS_ANDROID = False


# ============================================================
# 配色
# ============================================================
COLOR_BG = (0.10, 0.11, 0.14, 1)
COLOR_CARD = (0.16, 0.18, 0.22, 1)
COLOR_CARD_BORDER = (0.24, 0.27, 0.32, 1)
COLOR_PRIMARY = (0.29, 0.62, 1.0, 1)
COLOR_PRIMARY_DARK = (0.22, 0.48, 0.78, 1)
COLOR_SUCCESS = (0.30, 0.85, 0.45, 1)
COLOR_DANGER = (1.0, 0.36, 0.36, 1)
COLOR_DANGER_DARK = (0.75, 0.25, 0.25, 1)
COLOR_TEXT = (0.91, 0.92, 0.94, 1)
COLOR_TEXT_DIM = (0.55, 0.58, 0.65, 1)


# ============================================================
# WGS-84 → GCJ-02
# ============================================================
_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lat, lon):
    if lon < 72.004 or lon > 137.8347:
        return True
    if lat < 0.8293 or lat > 55.8271:
        return True
    return False


def _transform_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat, lon):
    if _out_of_china(lat, lon):
        return lat, lon
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + dlat, lon + dlon


# ============================================================
# 中文字体
# ============================================================
def register_chinese_font():
    candidates = []
    if IS_ANDROID:
        candidates.append('chinese.ttf')
        candidates.append(os.path.join(os.path.dirname(__file__), 'chinese.ttf'))
    else:
        if sys.platform == 'win32':
            candidates.extend([
                r'C:\Windows\Fonts\msyh.ttc',
                r'C:\Windows\Fonts\simhei.ttf',
            ])
        elif sys.platform == 'darwin':
            candidates.extend(['/System/Library/Fonts/PingFang.ttc'])
        else:
            candidates.extend(['/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'])
    for path in candidates:
        if os.path.exists(path):
            try:
                LabelBase.register(name='Roboto', fn_regular=path)
                print(f"[Font] 加载: {path}")
                return True
            except Exception:
                pass
    print("[Font] 未找到中文字体")
    return False

register_chinese_font()


# ============================================================
# 工具
# ============================================================
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def get_state_file_path():
    if IS_ANDROID:
        try:
            from jnius import autoclass
            PythonService = autoclass('org.kivy.android.PythonService')
            files_dir = PythonService.mService.getFilesDir().getAbsolutePath()
            return os.path.join(files_dir, 'state.json')
        except Exception as e:
            print(f"[Path] {e}")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state.json')


class StateReader:
    def __init__(self):
        self.path = get_state_file_path()
        self.last_mtime = 0
        self.last_state = {}

    def read(self):
        try:
            if not os.path.exists(self.path):
                return None
            mtime = os.path.getmtime(self.path)
            if mtime == self.last_mtime:
                return self.last_state
            with open(self.path, 'r') as f:
                self.last_state = json.load(f)
            self.last_mtime = mtime
            return self.last_state
        except Exception as e:
            print(f"[StateReader] {e}")
            return None


# ============================================================
# 圆角卡片
# ============================================================
class Card(BoxLayout):
    def __init__(self, bg_color=COLOR_CARD, border_color=COLOR_CARD_BORDER,
                 radius=14, **kwargs):
        super().__init__(**kwargs)
        self._radius = dp(radius)
        with self.canvas.before:
            Color(*bg_color)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size,
                                        radius=[self._radius])
            if border_color:
                Color(*border_color)
                self._border = Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height,
                                       self._radius),
                    width=1
                )
            else:
                self._border = None
        self.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        if self._border is not None:
            self._border.rounded_rectangle = (
                self.x, self.y, self.width, self.height, self._radius
            )


# ============================================================
# 圆角按钮
# ============================================================
class RoundedButton(Button):
    def __init__(self, bg_color=COLOR_PRIMARY,
                 bg_color_down=None, radius=12,
                 text_color=COLOR_TEXT, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.color = text_color
        self._radius = dp(radius)
        self._bg_color = bg_color
        if bg_color_down is None:
            bg_color_down = tuple(min(1, c * 0.75) for c in bg_color[:3]) + (bg_color[3],)
        self._bg_color_down = bg_color_down
        with self.canvas.before:
            Color(*bg_color)
            self._bg_rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[self._radius]
            )
        self.bind(pos=self._update_bg, size=self._update_bg,
                  state=self._update_state)

    def _update_bg(self, *args):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size

    def _update_state(self, *args):
        color = self._bg_color_down if self.state == 'down' else self._bg_color
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*color)
            self._bg_rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[self._radius]
            )

    def set_colors(self, bg, bg_down=None):
        self._bg_color = bg
        if bg_down is None:
            bg_down = tuple(min(1, c * 0.75) for c in bg[:3]) + (bg[3],)
        self._bg_color_down = bg_down
        self._update_state()


# ============================================================
# 圆形图标按钮
# ============================================================
class CircleIconButton(Button):
    def __init__(self, icon_type="locate", bg_color=(0.13, 0.15, 0.19, 0.92),
                 size_wh=(dp(46), dp(46)), on_press=None, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.size_hint = (None, None)
        self.size = size_wh
        self._icon_type = icon_type
        self._bg_color_normal = bg_color
        self._bg_color_down = (0.28, 0.32, 0.40, 0.95)
        if on_press:
            self.bind(on_press=lambda *a: on_press())
        self.bind(pos=self._redraw, size=self._redraw, state=self._redraw)
        self._redraw()

    def _redraw(self, *args):
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        w, h = self.size
        cx, cy = self.center_x, self.center_y
        r = min(w, h) / 2

        bg = self._bg_color_down if self.state == 'down' else self._bg_color_normal

        with self.canvas:
            Color(*bg)
            Ellipse(pos=self.pos, size=self.size)
            Color(1, 1, 1, 0.18)
            Line(circle=(cx, cy, r - 1), width=1)

            Color(*COLOR_TEXT)
            if self._icon_type == "locate":
                inner_r = r * 0.20
                Ellipse(
                    pos=(cx - inner_r, cy - inner_r),
                    size=(inner_r * 2, inner_r * 2)
                )
                ring_r = r * 0.52
                Line(points=[cx, cy - r * 0.72, cx, cy - ring_r], width=1.6)
                Line(points=[cx, cy + ring_r, cx, cy + r * 0.72], width=1.6)
                Line(points=[cx - r * 0.72, cy, cx - ring_r, cy], width=1.6)
                Line(points=[cx + ring_r, cy, cx + r * 0.72, cy], width=1.6)
            elif self._icon_type == "layer":
                s = r * 0.40
                offset = r * 0.16
                Color(1, 1, 1, 0.5)
                Line(
                    rectangle=(cx - s + offset, cy - s + offset, s * 2, s * 2),
                    width=1.6
                )
                Color(*COLOR_TEXT)
                Line(
                    rectangle=(cx - s - offset, cy - s - offset, s * 2, s * 2),
                    width=1.6
                )


# ============================================================
# 当前位置圆点图层（蓝心白边）
# ============================================================
class MapDotLayer(MapLayer):
    """在地图上画一个蓝心白边的圆点，作为当前位置标志"""

    def __init__(self, mapview, lat=0, lon=0, **kwargs):
        super().__init__(**kwargs)
        self.mapview = mapview
        self.lat = lat
        self.lon = lon
        # 点尺寸
        self._radius = dp(6)          # 主色半径
        self._outline = dp(2)         # 白色描边宽度

        self.mapview.bind(center=self.reposition,
                          zoom=self.reposition,
                          size=self.reposition)
        self.reposition()

    def set_position(self, lat, lon):
        self.lat = lat
        self.lon = lon
        self.reposition()

    def reposition(self, *args):
        self.canvas.clear()
        try:
            x, y = self.mapview.get_window_xy_from(
                self.lat, self.lon, self.mapview.zoom
            )
        except Exception:
            return

        total_r = self._radius + self._outline

        with self.canvas:
            # 白色外圈
            Color(1, 1, 1, 1)
            Ellipse(pos=(x - total_r, y - total_r),
                    size=(total_r * 2, total_r * 2))
            # 蓝色内芯
            Color(0.29, 0.62, 1.0, 1)
            Ellipse(pos=(x - self._radius, y - self._radius),
                    size=(self._radius * 2, self._radius * 2))


# ============================================================
# 地图轨迹图层
# ============================================================
class MapTrackLayer(MapLayer):
    def __init__(self, mapview, **kwargs):
        super().__init__(**kwargs)
        self.mapview = mapview
        self.points = []
        with self.canvas:
            Color(0.29, 0.62, 1.0, 0.95)
            self.line = Line(points=[], width=2.5)
        self.mapview.bind(center=self._on_map_change,
                          zoom=self._on_map_change,
                          size=self._on_map_change)
        self._on_map_change()

    def _on_map_change(self, *args):
        self.reposition()

    def add_point(self, lat, lon):
        self.points.append((lat, lon))
        if len(self.points) > 1000:
            self.points = self.points[-1000:]
        self.reposition()

    def clear_points(self):
        self.points = []
        self.reposition()

    def reposition(self, *args):
        if not self.points:
            self.line.points = []
            return
        pts = []
        for lat, lon in self.points:
            try:
                x, y = self.mapview.get_window_xy_from(
                    lat, lon, self.mapview.zoom
                )
                pts.extend([x, y])
            except Exception:
                pass
        self.line.points = pts if len(pts) >= 4 else []


# ============================================================
# 图层源（高德）
# ============================================================
VECTOR_SOURCE = MapSource(
    url="https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    max_zoom=18,
    attribution="© 高德地图",
)
SATELLITE_SOURCE = MapSource(
    url="https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
    max_zoom=18,
    attribution="© 高德地图",
)


# ============================================================
# 状态行
# ============================================================
class StatusRow(BoxLayout):
    def __init__(self, label_text, value_text="—",
                 label_color=COLOR_TEXT_DIM,
                 value_color=COLOR_TEXT,
                 label_width=0.24,
                 dot_color=None,
                 **kwargs):
        super().__init__(orientation='horizontal',
                         size_hint_y=None, height=dp(28),
                         spacing=dp(6), **kwargs)
        self._dot_color = dot_color

        if dot_color is not None:
            self.dot = Widget(size_hint_x=None, width=dp(12))
            with self.dot.canvas:
                Color(*dot_color)
                self._dot_circle = Ellipse(
                    pos=(self.dot.center_x - dp(4), self.dot.center_y - dp(4)),
                    size=(dp(8), dp(8))
                )
            self.dot.bind(
                pos=lambda *a: self._update_dot(),
                size=lambda *a: self._update_dot(),
            )
            self.add_widget(self.dot)

        self.label = Label(
            text=label_text, font_size=dp(13),
            color=label_color,
            size_hint_x=label_width,
            halign='left', valign='middle'
        )
        self.label.bind(
            size=lambda *a: setattr(self.label, 'text_size',
                                    (self.label.width, None))
        )
        self.add_widget(self.label)

        self.value = Label(
            text=value_text, font_size=dp(14),
            color=value_color,
            halign='left', valign='middle'
        )
        self.value.bind(
            size=lambda *a: setattr(self.value, 'text_size',
                                    (self.value.width, None))
        )
        self.add_widget(self.value)

    def _update_dot(self):
        self._dot_circle.pos = (
            self.dot.center_x - dp(4), self.dot.center_y - dp(4)
        )

    def set_value(self, text, color=None):
        self.value.text = text
        if color is not None:
            self.value.color = color

    def set_dot_color(self, color):
        self.dot.canvas.clear()
        with self.dot.canvas:
            Color(*color)
            self._dot_circle = Ellipse(
                pos=(self.dot.center_x - dp(4), self.dot.center_y - dp(4)),
                size=(dp(8), dp(8))
            )


# ============================================================
# 主 UI
# ============================================================
class ReceiverUI(FloatLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        with self.canvas.before:
            Color(*COLOR_BG)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda *a: setattr(self._bg_rect, 'pos', self.pos),
            size=lambda *a: setattr(self._bg_rect, 'size', self.size),
        )

        main = BoxLayout(
            orientation='vertical',
            padding=[dp(12), dp(14), dp(12), dp(10)],
            spacing=dp(10),
            size_hint=(1, 1),
            pos_hint={'x': 0, 'y': 0},
        )
        self.add_widget(main)

        # ---------- 标题 ----------
        title_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        title = Label(
            text="GPS 模拟位置", font_size=dp(18),
            color=COLOR_TEXT, bold=True,
            halign='left', valign='middle',
        )
        title.bind(size=lambda *a: setattr(title, 'text_size', (title.width, None)))
        title_row.add_widget(title)

        self.status_badge = Label(
            text="未启动", font_size=dp(12),
            color=COLOR_TEXT_DIM,
            size_hint_x=None, width=dp(70),
            halign='right', valign='middle',
        )
        self.status_badge.bind(
            size=lambda *a: setattr(self.status_badge, 'text_size',
                                    (self.status_badge.width, None))
        )
        title_row.add_widget(self.status_badge)
        main.add_widget(title_row)

        # ---------- 状态卡片 ----------
        card = Card(size_hint_y=None, height=dp(138),
                    orientation='vertical',
                    padding=[dp(14), dp(10), dp(14), dp(10)],
                    spacing=dp(2))

        self.row_status = StatusRow("状态", "未启动",
                                    dot_color=COLOR_TEXT_DIM,
                                    label_width=0.22)
        card.add_widget(self.row_status)

        self.row_coord = StatusRow("位置", "—", label_width=0.22)
        card.add_widget(self.row_coord)

        self.row_count = StatusRow("已发送", "0 点", label_width=0.22)
        card.add_widget(self.row_count)

        self.row_ip = StatusRow("本机 IP", get_local_ip(),
                                label_width=0.22,
                                value_color=COLOR_PRIMARY)
        card.add_widget(self.row_ip)

        main.add_widget(card)

        # ---------- 地图 ----------
        map_card = Card(
            orientation='vertical',
            padding=[dp(2), dp(2), dp(2), dp(2)],
            spacing=0,
            radius=14,
            size_hint_y=1,
        )

        map_container = FloatLayout(size_hint=(1, 1))

        init_lat, init_lon = wgs84_to_gcj02(39.9042, 116.4074)
        self.mapview = MapView(
            zoom=16, lat=init_lat, lon=init_lon,
            map_source=VECTOR_SOURCE,
            size_hint=(1, 1),
            pos_hint={'x': 0, 'y': 0},
        )
        map_container.add_widget(self.mapview)

        map_card.add_widget(map_container)
        main.add_widget(map_card)

        # ---------- 图层：轨迹 + 当前点 ----------
        # 轨迹图层（在下面，先加）
        self._track_line = MapTrackLayer(self.mapview)
        self.mapview.add_widget(self._track_line)
        self._track_line.pos = (0, 0)
        self._track_line.size = self.mapview.size
        self.mapview.bind(
            size=lambda inst, val: setattr(self._track_line, 'size', val)
        )

        # 当前点图层（在上面，后加）
        self._dot_layer = MapDotLayer(self.mapview, lat=init_lat, lon=init_lon)
        self.mapview.add_widget(self._dot_layer)
        self._dot_layer.pos = (0, 0)
        self._dot_layer.size = self.mapview.size
        self.mapview.bind(
            size=lambda inst, val: setattr(self._dot_layer, 'size', val)
        )

        self._map_card = map_card

        # ---------- 底部 ----------
        bottom_card = Card(
            orientation='vertical',
            size_hint_y=None, height=dp(104),
            padding=[dp(14), dp(12), dp(14), dp(10)],
            spacing=dp(8),
        )

        row1 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        port_label = Label(
            text="端口", font_size=dp(13),
            color=COLOR_TEXT_DIM,
            size_hint_x=None, width=dp(40),
            halign='left', valign='middle',
        )
        port_label.bind(size=lambda *a: setattr(port_label, 'text_size',
                                                (port_label.width, None)))
        row1.add_widget(port_label)

        self.port_input = TextInput(
            text="8888", multiline=False, input_filter='int',
            size_hint_x=None, width=dp(90),
            font_size=dp(16),
            background_color=(0.10, 0.11, 0.14, 1),
            foreground_color=COLOR_TEXT,
            cursor_color=COLOR_PRIMARY,
            padding=[dp(10), dp(10)],
        )
        row1.add_widget(self.port_input)

        self.btn = RoundedButton(
            text="启动服务",
            size_hint_x=1,
            font_size=dp(16),
            bg_color=COLOR_PRIMARY,
            bold=True,
        )
        self.btn.bind(on_press=self.toggle_service)
        row1.add_widget(self.btn)
        bottom_card.add_widget(row1)

        self.hint = Label(
            text="请先到 开发者选项 → 模拟位置应用 中选中本 App",
            size_hint_y=None, height=dp(18),
            halign='center', valign='middle',
            font_size=dp(11),
            color=COLOR_TEXT_DIM,
        )
        self.hint.bind(size=lambda *a: setattr(self.hint, 'text_size',
                                               (self.hint.width, None)))
        bottom_card.add_widget(self.hint)

        main.add_widget(bottom_card)

        # ---------- 悬浮按钮 ----------
        btn_size = dp(46)
        self._btn_locate = CircleIconButton(
            icon_type="locate",
            bg_color=(0.13, 0.15, 0.19, 0.92),
            size_wh=(btn_size, btn_size),
            on_press=self.locate_current,
        )
        self._btn_layer = CircleIconButton(
            icon_type="layer",
            bg_color=(0.13, 0.15, 0.19, 0.92),
            size_wh=(btn_size, btn_size),
            on_press=self.toggle_layer,
        )
        self.add_widget(self._btn_locate)
        self.add_widget(self._btn_layer)

        self._btn_size = btn_size

        map_card.bind(pos=self._position_buttons,
                      size=self._position_buttons)
        Clock.schedule_once(self._position_buttons, 0)
        Clock.schedule_once(self._position_buttons, 0.3)

        # ---------- 状态 ----------
        self.service_running = False
        self.state_reader = StateReader()
        self.last_count = 0
        self.track_points = []
        self.current_lat = init_lat
        self.current_lon = init_lon
        self._current_layer = 'vector'
        self.service_process = None

        Clock.schedule_once(self._initial_center, 0.5)
        Clock.schedule_interval(self.refresh, 0.5)

    def _position_buttons(self, *args):
        mc = self._map_card
        if mc.width <= 0 or mc.height <= 0:
            return
        size = self._btn_size
        margin = dp(14)
        gap = dp(12)
        x = mc.right - size - margin
        y_layer = mc.y + margin
        y_locate = y_layer + size + gap
        self._btn_layer.pos = (x, y_layer)
        self._btn_locate.pos = (x, y_locate)

    def _initial_center(self, dt):
        state = self.state_reader.read()
        if state and state.get('lat') is not None:
            lat, lon = wgs84_to_gcj02(state['lat'], state['lon'])
            self.current_lat = lat
            self.current_lon = lon
            self.mapview.center_on(lat, lon)
            self._dot_layer.set_position(lat, lon)

    def locate_current(self, *args):
        self.mapview.center_on(self.current_lat, self.current_lon)

    def toggle_layer(self, *args):
        if self._current_layer == 'vector':
            self.mapview.map_source = SATELLITE_SOURCE
            self._current_layer = 'satellite'
        else:
            self.mapview.map_source = VECTOR_SOURCE
            self._current_layer = 'vector'

    def toggle_service(self, *args):
        if not self.service_running:
            try:
                port = int(self.port_input.text)
            except ValueError:
                self.row_status.set_value("端口必须为数字", COLOR_DANGER)
                return
            ok = self.start_service(port)
            if ok:
                self.service_running = True
                self.btn.text = "停止服务"
                self.btn.set_colors(COLOR_DANGER, COLOR_DANGER_DARK)
                self.status_badge.text = "运行中"
                self.status_badge.color = COLOR_SUCCESS
                self.row_status.set_value(f"监听端口 {port}", COLOR_SUCCESS)
                self.row_status.set_dot_color(COLOR_SUCCESS)
            else:
                self.row_status.set_value("启动失败，看日志", COLOR_DANGER)
                self.row_status.set_dot_color(COLOR_DANGER)
        else:
            self.stop_service()
            self.service_running = False
            self.btn.text = "启动服务"
            self.btn.set_colors(COLOR_PRIMARY, COLOR_PRIMARY_DARK)
            self.status_badge.text = "未启动"
            self.status_badge.color = COLOR_TEXT_DIM
            self.row_status.set_value("未启动", COLOR_TEXT_DIM)
            self.row_status.set_dot_color(COLOR_TEXT_DIM)
            self.clear_track()
            self.row_coord.set_value("—")
            self.row_count.set_value("0 点")
            self.last_count = 0

    def start_service(self, port):
        if IS_ANDROID:
            try:
                from jnius import autoclass
                from android import mActivity
                Intent = autoclass('android.content.Intent')
                service = autoclass('com.example.gpsreceiver.ServiceReceiver')
                mActivity.startService(
                    Intent(mActivity, service).putExtra("port", port)
                )
                return True
            except Exception as e:
                print(f"[Service] 启动失败: {e}")
                return False
        else:
            try:
                svc_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    'service', 'main.py'
                )
                if not os.path.exists(svc_path):
                    print(f"[Service] 找不到 {svc_path}")
                    return False
                self.service_process = subprocess.Popen(
                    [sys.executable, svc_path, "--port", str(port)],
                    cwd=os.path.dirname(svc_path),
                )
                print(f"[Service] 已启动子进程 PID={self.service_process.pid}")
                return True
            except Exception as e:
                print(f"[Service] subprocess 启动失败: {e}")
                return False

    def stop_service(self):
        if IS_ANDROID:
            try:
                from jnius import autoclass
                from android import mActivity
                Intent = autoclass('android.content.Intent')
                service = autoclass('com.example.gpsreceiver.ServiceReceiver')
                mActivity.stopService(Intent(mActivity, service))
            except Exception as e:
                print(f"[Service] 停止失败: {e}")
        else:
            if self.service_process is not None:
                try:
                    if sys.platform == 'win32':
                        self.service_process.terminate()
                    else:
                        self.service_process.send_signal(signal.SIGINT)
                    self.service_process.wait(timeout=3)
                except Exception as e:
                    print(f"[Service] 停止子进程失败: {e}")
                    try:
                        self.service_process.kill()
                    except Exception:
                        pass
                self.service_process = None
                print("[Service] 子进程已停止")

    def refresh(self, dt):
        if not self.service_running:
            self.row_ip.set_value(get_local_ip(), COLOR_PRIMARY)
            return

        state = self.state_reader.read()
        if not state:
            return

        raw_lat = state.get('lat')
        raw_lon = state.get('lon')
        count = state.get('count', 0)

        if raw_lat is not None and raw_lon is not None:
            self.row_coord.set_value(f"{raw_lat:.6f}, {raw_lon:.6f}")
            if count > self.last_count:
                lat, lon = wgs84_to_gcj02(raw_lat, raw_lon)
                self.add_track_point(lat, lon)

        self.row_count.set_value(f"{count} 点")
        self.last_count = count

    def add_track_point(self, lat, lon):
        self.track_points.append((lat, lon))
        if len(self.track_points) > 500:
            self.track_points = self.track_points[-500:]

        self.current_lat = lat
        self.current_lon = lon
        self.mapview.center_on(lat, lon)

        # 更新轨迹
        self._track_line.add_point(lat, lon)
        # 更新当前位置蓝点
        self._dot_layer.set_position(lat, lon)

    def clear_track(self):
        self.track_points = []
        self._track_line.clear_points()


class ReceiverApp(App):
    def build(self):
        self.title = "GPS Receiver"
        return ReceiverUI()

    def on_stop(self):
        if hasattr(self, 'root') and self.root is not None:
            if self.root.service_running:
                self.root.stop_service()


if __name__ == "__main__":
    ReceiverApp().run()