"""
Web 地图查看器 + 路线编辑器
- 打点模式：单击地图添加路径点（允许平移地图）
- 画笔模式：按住鼠标拖动连续画点（禁用地图拖动）
- 浏览模式：正常拖动/缩放
- 模拟模式：不连手机，仅网页演示
- 定位：优先用浏览器 Geolocation（Windows 位置服务），失败退回 IP 定位
零第三方依赖。
"""

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


STATE = {
    "route": [],
    "current": None,
    "status": "空闲",
    "running": False,
    "home": None,
}
_lock = threading.Lock()

_playback_fn = None
_playback_stop_event = threading.Event()
_playback_thread = None


def update_state(**kwargs):
    with _lock:
        STATE.update(kwargs)


def get_state():
    with _lock:
        return dict(STATE)


def set_playback_handler(fn):
    global _playback_fn
    _playback_fn = fn


def _start_playback(config):
    global _playback_thread
    _playback_stop_event.clear()
    if _playback_fn is None:
        update_state(status="未注册回放函数")
        return
    _playback_thread = threading.Thread(
        target=_playback_fn,
        args=(config, _playback_stop_event),
        daemon=True,
    )
    _playback_thread.start()


def _stop_playback():
    _playback_stop_event.set()
    update_state(status="已停止", running=False)


# ============================================================
# IP 定位（仅作浏览器定位失败时的兜底）
# ============================================================
def get_ip_location():
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/",
            headers={'User-Agent': 'RoutePlayer/1.0'},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            d = json.loads(resp.read().decode('utf-8'))
            if d.get('status') == 'success':
                return {
                    "ok": True,
                    "lat": float(d['lat']),
                    "lon": float(d['lon']),
                    "city": d.get('city', ''),
                    "country": d.get('country', ''),
                    "source": "IP 定位",
                }
    except Exception as e:
        print(f"[IPLoc] 失败: {e}")
    return {"ok": False, "error": "IP 定位失败"}


HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>GPS 路线回放 - 编辑器</title>
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body, #map { height: 100%; margin: 0; }
  #toolbar {
    position: absolute; top: 10px; right: 10px; z-index: 1000;
    background: rgba(255,255,255,0.96); padding: 10px 12px;
    border-radius: 6px; font-family: sans-serif; font-size: 13px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.3); min-width: 240px;
  }
  #toolbar b { display: block; margin-bottom: 6px; }
  #toolbar button {
    margin: 2px 2px; padding: 4px 8px; cursor: pointer;
    border: 1px solid #ccc; border-radius: 4px; background: #f7f7f7;
  }
  #toolbar button.active { background: #1976d2; color: #fff; border-color: #1976d2; }
  #toolbar button:disabled { opacity: 0.5; cursor: wait; }
  #toolbar input[type=text], #toolbar input[type=number] {
    width: 90px; padding: 3px 5px; border: 1px solid #ccc; border-radius: 3px;
  }
  #toolbar hr { border: none; border-top: 1px solid #eee; margin: 6px 0; }
  #status { color: #666; font-size: 12px; }
  .row { margin: 3px 0; }
  .sim-row { background: #fff8e1; padding: 4px 6px; border-radius: 4px; margin: 4px 0; }
  .sim-row label { font-weight: bold; color: #b26a00; }
  .net-fields.disabled { opacity: 0.4; pointer-events: none; }
  .hint { font-size: 11px; color: #888; margin-top: 4px; }
  .home-row { margin: 4px 0; }
  #locate { width: 100%; margin: 2px 0; }
  #home-info { font-size: 11px; color: #666; margin-top: 3px; word-break: break-all; }
</style>
</head>
<body>
<div id="map"></div>

<div id="toolbar">
  <b>路线编辑器</b>

  <div class="row">
    <button id="mode-click">打点</button>
    <button id="mode-draw">画笔</button>
    <button id="mode-view">浏览</button>
  </div>
  <div class="hint" id="mode-hint">单击地图加一个路径点</div>

  <div class="row">
    <button id="undo">撤销</button>
    <button id="clear">清空</button>
  </div>

  <hr>

  <div class="home-row">
    <button id="locate">📍 定位到我的位置</button>
    <div id="home-info"></div>
  </div>

  <hr>

  <div class="sim-row">
    <label><input type="checkbox" id="sim_mode"> 模拟模式（不连手机）</label>
  </div>

  <div id="net-fields">
    <div class="row">手机 IP: <input type="text" id="host" value="192.168.1.100"></div>
    <div class="row">端口: <input type="number" id="port" value="8888"></div>
  </div>

  <div class="row">速度: <input type="number" id="speed" value="30"> km/h</div>
  <div class="row"><label><input type="checkbox" id="loop"> 循环回放</label></div>

  <div class="row">
    <button id="start">开始回放</button>
    <button id="stop">停止</button>
  </div>

  <hr>
  <div class="row">状态: <span id="status">空闲</span></div>
  <div class="row" style="font-family:monospace;font-size:11px;">
    当前: <span id="coord">—</span>
  </div>
  <div class="row" style="font-family:monospace;font-size:11px;">
    路径点: <span id="count">0</span>
  </div>
</div>

<script>
const map = L.map('map').setView([39.9042, 116.4074], 14);

const vecLayer = L.tileLayer(
  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  { maxZoom: 19, attribution: '© OpenStreetMap' }
);
const satLayer = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, attribution: '© Esri' }
);
vecLayer.addTo(map);
L.control.layers(
  { "矢量图": vecLayer, "卫星图": satLayer },
  null,
  { position: 'topleft' }
).addTo(map);

let route = [];
let mode = 'click';
let drawing = false;
let lastDrawPoint = null;
const DRAW_PIXEL_THRESHOLD = 6;

let routeLine = null;
let routeMarkers = [];
let currentMarker = null;
let trailLine = null;
let trailPoints = [];
let homeMarker = null;

// ============ 渲染路线 ============
function renderRoute() {
  routeMarkers.forEach(m => map.removeLayer(m));
  routeMarkers = [];
  if (routeLine) { map.removeLayer(routeLine); routeLine = null; }

  if (route.length === 0) {
    document.getElementById('count').textContent = '0';
    return;
  }

  route.forEach(p => {
    const m = L.circleMarker([p.lat, p.lon], {
      radius: 4, color: '#1976d2', fillColor: '#fff',
      fillOpacity: 1, weight: 2
    }).addTo(map);
    routeMarkers.push(m);
  });

  if (route.length >= 2) {
    const latlngs = route.map(p => [p.lat, p.lon]);
    routeLine = L.polyline(latlngs, {
      color: '#1976d2', weight: 3, opacity: 0.85
    }).addTo(map);
  }

  document.getElementById('count').textContent = route.length;
}

// ============ 当前定位点 ============
function updateCurrent(pt) {
  if (!pt) return;
  if (!currentMarker) {
    currentMarker = L.circleMarker([pt.lat, pt.lon], {
      radius: 8, color: '#d32f2f', fillColor: '#d32f2f',
      fillOpacity: 0.6, weight: 3
    }).addTo(map);
  } else {
    currentMarker.setLatLng([pt.lat, pt.lon]);
  }
  document.getElementById('coord').textContent =
    pt.lat.toFixed(6) + ', ' + pt.lon.toFixed(6);
}

// ============ 尾迹 ============
function clearTrail() {
  if (trailLine) { map.removeLayer(trailLine); trailLine = null; }
  trailPoints = [];
}
function addTrailPoint(pt) {
  trailPoints.push([pt.lat, pt.lon]);
  if (!trailLine) {
    trailLine = L.polyline(trailPoints, {
      color: '#ff6f00', weight: 2, opacity: 0.7, dashArray: '4,4'
    }).addTo(map);
  } else {
    trailLine.setLatLngs(trailPoints);
  }
}

// ============ 模式切换 ============
const HINTS = {
  click: '单击地图加一个路径点；拖动地图正常平移',
  draw: '按住鼠标拖动连续画点；地图已锁定，切"浏览"可平移',
  view: '正常拖动/缩放地图',
};
function setMode(m) {
  mode = m;
  document.getElementById('mode-click').classList.toggle('active', m === 'click');
  document.getElementById('mode-draw').classList.toggle('active', m === 'draw');
  document.getElementById('mode-view').classList.toggle('active', m === 'view');
  document.getElementById('mode-hint').textContent = HINTS[m] || '';
  if (m === 'draw') {
    map.dragging.disable();
    map.getContainer().style.cursor = 'crosshair';
  } else {
    map.dragging.enable();
    map.getContainer().style.cursor =
      m === 'click' ? 'crosshair' : 'grab';
  }
}
document.getElementById('mode-click').onclick = () => setMode('click');
document.getElementById('mode-draw').onclick  = () => setMode('draw');
document.getElementById('mode-view').onclick  = () => setMode('view');

// ============ 模拟模式开关 ============
const simCheckbox = document.getElementById('sim_mode');
const netFields = document.getElementById('net-fields');
function syncSimMode() {
  netFields.classList.toggle('disabled', simCheckbox.checked);
}
simCheckbox.onchange = syncSimMode;
syncSimMode();

// ============ 地图交互 ============
map.on('click', (e) => {
  if (mode !== 'click') return;
  route.push({ lat: e.latlng.lat, lon: e.latlng.lng });
  renderRoute();
});

map.on('mousedown', (e) => {
  if (mode !== 'draw') return;
  drawing = true;
  lastDrawPoint = e.containerPoint;
  route.push({ lat: e.latlng.lat, lon: e.latlng.lng });
  renderRoute();
});

map.on('mousemove', (e) => {
  if (!drawing) return;
  if (!lastDrawPoint) { lastDrawPoint = e.containerPoint; return; }
  const dx = e.containerPoint.x - lastDrawPoint.x;
  const dy = e.containerPoint.y - lastDrawPoint.y;
  if (Math.sqrt(dx * dx + dy * dy) < DRAW_PIXEL_THRESHOLD) return;
  lastDrawPoint = e.containerPoint;
  route.push({ lat: e.latlng.lat, lon: e.latlng.lng });
  renderRoute();
});

map.on('mouseup', () => {
  if (drawing) { drawing = false; lastDrawPoint = null; }
});
map.on('mouseout', () => {
  if (drawing) { drawing = false; lastDrawPoint = null; }
});

// ============ 撤销 / 清空 ============
document.getElementById('undo').onclick = () => { route.pop(); renderRoute(); };
document.getElementById('clear').onclick = () => {
  route = [];
  if (currentMarker) { map.removeLayer(currentMarker); currentMarker = null; }
  clearTrail();
  renderRoute();
};

// ============ 定位 ============
function showHome(lat, lon, info) {
  if (homeMarker) map.removeLayer(homeMarker);
  homeMarker = L.circleMarker([lat, lon], {
    radius: 10, color: '#4caf50', fillColor: '#4caf50',
    fillOpacity: 0.5, weight: 3
  }).addTo(map);
  homeMarker.bindPopup(
    `<b>我的位置</b><br>${lat.toFixed(5)}, ${lon.toFixed(5)}<br>` +
    `<small>${info || ''}</small>`
  ).openPopup();
  map.setView([lat, lon], 16);
  document.getElementById('home-info').textContent =
    `${lat.toFixed(5)}, ${lon.toFixed(5)} · ${info || ''}`;
}

function browserLocate() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('浏览器不支持 Geolocation'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      }),
      (err) => reject(err),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
  });
}

async function locateMe() {
  const btn = document.getElementById('locate');
  const old = btn.textContent;
  btn.textContent = '定位中…';
  btn.disabled = true;
  document.getElementById('home-info').textContent = '请求浏览器位置权限…';

  // 1. 先试浏览器 Geolocation
  try {
    const p = await browserLocate();
    const acc = p.accuracy ? `±${Math.round(p.accuracy)}m` : '';
    showHome(p.lat, p.lon, `浏览器定位 ${acc}`);
    return;
  } catch (e) {
    console.warn('浏览器定位失败:', e);
    document.getElementById('home-info').textContent =
      `浏览器定位失败 (${e.message || e.code})，尝试 IP 定位…`;
  }

  // 2. 兜底：IP 定位
  try {
    const r = await fetch('/api/ip-location');
    const d = await r.json();
    if (d.ok) {
      showHome(d.lat, d.lon,
        `${d.city || ''} ${d.country || ''} · IP 定位（城市级）`);
    } else {
      document.getElementById('home-info').textContent =
        '定位失败：浏览器拒绝 + IP 定位失败';
    }
  } catch (e) {
    document.getElementById('home-info').textContent = '请求失败: ' + e;
  } finally {
    btn.textContent = old;
    btn.disabled = false;
  }
}

document.getElementById('locate').onclick = locateMe;

// ============ 开始 / 停止 ============
document.getElementById('start').onclick = async () => {
  if (route.length < 2) {
    alert('请至少添加 2 个路径点');
    return;
  }
  const isSim = simCheckbox.checked;
  const cfg = {
    route: route,
    host: document.getElementById('host').value.trim(),
    port: parseInt(document.getElementById('port').value),
    speed: parseFloat(document.getElementById('speed').value),
    loop: document.getElementById('loop').checked,
    dry_run: isSim,
  };
  clearTrail();
  await fetch('/api/route', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(cfg.route)
  });
  await fetch('/api/start', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(cfg)
  });
};

document.getElementById('stop').onclick = async () => {
  await fetch('/api/stop', { method: 'POST' });
};

// ============ 状态轮询 ============
async function poll() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    document.getElementById('status').textContent = s.status;
    if (s.current) {
      updateCurrent(s.current);
      if (simCheckbox.checked) addTrailPoint(s.current);
    }
  } catch (e) {
    document.getElementById('status').textContent = '连接失败';
  }
}
setInterval(poll, 800);
poll();

setMode('click');
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            body = HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/api/state':
            self._send_json(get_state())
        elif self.path == '/api/ip-location':
            result = get_ip_location()
            self._send_json(result)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length) if length else b''

        if self.path == '/api/route':
            try:
                route = json.loads(raw.decode('utf-8'))
                update_state(route=route)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif self.path == '/api/start':
            try:
                cfg = json.loads(raw.decode('utf-8')) if raw else {}
                _start_playback(cfg)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif self.path == '/api/stop':
            _stop_playback()
            self._send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


def start_server(port=8000):
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[Viewer] 地图编辑器已启动: http://localhost:{port}")
    return server