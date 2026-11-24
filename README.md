# Fake_GPS

Android 定位模拟工具。通过 PC 端发送坐标，手机端接收后注入系统定位，支持沿 GPX 路径移动和定点漂移两种模式。

## 功能

- **路线模式**：沿 GPX 路径按指定速度移动，模拟真实移动轨迹
- **定点模式**：在指定中心点附近随机漂移
- **实时可视化**：PC 端提供网页界面，显示当前坐标和行进轨迹
- **前台服务**：手机端以 Android 前台服务运行，监控 mock provider 状态，被系统移除后自动重建

## 目录结构

```
Fake_GPS/
├─ app/                     手机端（Kivy）
│  ├─ main.py               界面：地图、路线、端口配置
│  ├─ service/main.py       前台服务：监听 TCP、注入定位
│  └─ buildozer.spec        APK 打包配置
├─ server/                  PC 端
│  ├─ main.py               路线回放引擎
│  └─ viewer.py             网页可视化
└─ .github/workflows/       CI 配置
```

## 使用

### 环境要求

- PC 端：Python 3.8+，仅使用标准库
- 手机端：Android 8.0（API 26）及以上
- 手机与 PC 处于同一局域网

### PC 端

直接运行即可启动网页控制面板：

```bash
cd server
python main.py
```

浏览器打开 `http://127.0.0.1:8000`，在网页里选择 GPX 路径、设置速度和目标 IP，点击开始即可。默认监听 8000 端口。

也可以带参数启动，省去在页面上填写：

```bash
python main.py --gpx route.gpx --speed 30 --host 192.168.1.100 --port 8888
```

参数说明：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--gpx` | 无 | GPX 路径文件，不传则使用定点模式 |
| `--speed` | 30.0 | 移动速度，单位 km/h |
| `--host` | 192.168.1.100 | 手机端 IP 地址 |
| `--port` | 8888 | 手机端监听端口 |
| `--loop` | 关闭 | 路径走完后循环 |
| `--dry-run` | 关闭 | 仅计算坐标，不发送 |
| `--web-port` | 8000 | 网页面板端口，设为 0 则退回命令行模式 |
| `--cli` | 关闭 | 不启动网页，仅命令行输出 |

### 手机端

1. 安装 APK
2. 在开发者选项中将本应用设为「模拟位置信息应用」
3. 填写 PC 端 IP 和端口，点击「启动服务」

界面唯一按钮控制服务的启停，无需连接电脑或输入命令。服务启动后会创建 Android 前台服务，监听 TCP 端口接收坐标并注入系统定位。运行期间每 100ms 写入一次坐标，同时定期检查 mock provider 是否被系统移除，被移除时自动重建。服务持有唤醒锁，避免后台被系统冻结。

### 打包 APK

推送到 `master` 分支会触发 GitHub Actions 自动构建：

- 环境：Ubuntu 22.04、JDK 17、Python 3.11.5
- 使用 Buildozer 构建 debug APK
- 缓存 `~/.buildozer` 目录，后续构建提速
- 产物在 Actions 的 `apk` artifact 中

本地构建：

```bash
cd app
buildozer android debug
```

需要 Linux 环境（Windows 下可用 WSL 或 Docker）。

## 原理

### 轨迹生成

普通定位工具的坐标是固定值，缺少真实设备的数据特征。本项目在坐标上叠加了三项扰动：

| 扰动 | 参数 | 说明 |
|---|---|---|
| 横向偏移 | `DEFAULT_LATERAL_MAX = 1.0` | 沿路径行进时，垂直方向最多偏移 1 米 |
| 高斯漂移 | `DRIFT_STD_MIN/MAX = 1.5 / 3.0` | 在当前位置叠加 σ 为 1.5–3 米的正态扰动 |
| 随机停顿 | `PAUSE_PROB = 0.05`，停 2–4 秒 | 以 5% 概率暂停，模拟路口等待 |

上报间隔同样不固定，在 0.6–1.8 秒之间随机取值，接近真实设备的上报节奏。

### 路径插值

沿 GPX 路径推进时，用 haversine 公式计算两点间距离，按速度换算出的已行进距离在路径上插值，得到当前坐标。

### 高频写入

Android 系统在切换定位模式或触发省电策略时，可能移除 mock provider。100ms 的写入频率配合定期检测，可以在 provider 被移除后立即重建，避免坐标回落到真实位置。

### 依赖

PC 端只使用 Python 标准库（`socket`、`math`、`random`、`threading`、`xml.etree`），无第三方依赖。

## 权限

APK 声明以下权限：

```
ACCESS_FINE_LOCATION          精确定位
ACCESS_COARSE_LOCATION        粗略定位
ACCESS_MOCK_LOCATION          模拟位置
FOREGROUND_SERVICE            前台服务
FOREGROUND_SERVICE_LOCATION   定位类前台服务
POST_NOTIFICATIONS            前台服务通知
INTERNET                      与 PC 端通信
```

## 已知限制

- 仅支持 GPX 格式路径文件
- 手机与 PC 需处于同一局域网
- 目前为 debug 签名，未配置发布签名

## 许可证

MIT License