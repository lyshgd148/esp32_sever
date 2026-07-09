# ESP32 Sever

基于 Flask + SocketIO 的 ESP32 远程控制服务端，支持灯带颜色、电机、红外学习/发射、OTA 固件升级、STM32 烧录。

## 架构

```
┌──────────────────────┐       TCP :9000       ┌─────────────────┐
│   PC Server          │ ◄───────────────────► │   ESP32 Device   │
│   Flask + SocketIO   │   文本协议 + OTA帧     │   MicroPython    │
│                      │                       │                  │
│   :5000 HTTP/WS      │                       │   WS2812 灯带    │
│   :9000 TCP          │                       │   电机控制        │
└──────────┬───────────┘                       │   红外收发        │
           │ WebSocket                         └─────────────────┘
           │
    ┌──────┴──────┐
    │   浏览器     │
    │   Web UI    │
    └─────────────┘
```

### 服务端 (`app/`)

| 模块 | 职责 |
|------|------|
| `app/__init__.py` | Flask 工厂 + 认证中间件 + 心跳看门狗 |
| `app/tcp.py` | TCP 服务器 (端口 9000)，处理 ESP32 连接 |
| `app/events.py` | WebSocket 事件 (connect / color / motor / IR) |
| `app/ota.py` | OTA 固件传输 + STM32 烧录调度 |
| `app/state.py` | 全局共享状态 (线程安全) |
| `app/utils/db.py` | MySQL 连接池 (DBUtils PooledDB) |
| `app/views/` | Flask 蓝图路由 (主页/颜色/上传/红外/遥控/账户) |
| `app/templates/` | Jinja2 前端页面模板 |
| `app/static/` | Bootstrap 5.3 静态资源 |

### ESP32 端 (`ESP32/`)

```
ESP32/
├── main.py                  # 入口 + 双线程管理
└── esp32sever/
    ├── config.py            # WiFi / 服务器 / 协议常量
    ├── connection.py        # NetworkManager: WiFi + TCP + 安全发送
    ├── protocol.py          # 二进制帧解析 (OTA ACK/DATA/START...)
    ├── ota.py               # OTA 状态机 + STM32 烧录
    ├── hardware.py          # WS2812 灯带 / 电机 / 编码器模拟
    ├── ir_handler.py        # 红外学习 / 发射包装
    ├── infrared.py          # 红外底层驱动 (IRLearner)
    ├── STM32DOWN.py         # STM32 ISP 烧录协议
    └── crc.py               # CRC16 / CRC32 查表
```

#### ESP32 双线程架构

```
Receiver 线程:                    Main 线程:
  sock.recv() → 解析帧/行         send_lock 保护 sock.send()
  → 命令分发 → 直接回复            ├─ PING 心跳 (10s)
  断连 → need_reconnect 标志      ├─ PONG 超时 (15s)
  退出线程                         ├─ DATA 数据流
                                  ├─ need_reconnect → 重连
                                  └─ gc.collect()
```

## 通信协议

### TCP 文本协议 (`\n` 分隔)

| 方向 | 指令 | 说明 |
|------|------|------|
| E→S | `PING` | 心跳请求 |
| S→E | `PONG` | 心跳响应 |
| E→S | `STATE,motor=,angle=,speed=` | 状态上报 |
| E→S | `DATA,angle=,speed=` | 实时数据流 |
| S→E | `GET_STATE` / `START_DATA` / `STOP_DATA` | 数据流控制 |
| S→E | `MOTOR_START` / `MOTOR_STOP` | 电机控制 |
| S→E | `COLOR=R,G,B` | 灯带颜色 |
| S→E | `LEARN_IR` | 红外学习 |
| S→E | `SEND_IR=x,x,x,...` | 红外发射 |
| S→E | `OTA_START` / `OTA_READY` / `OTA_ERROR` | OTA 准备 |
| S→E | `FLASH_BIN` | STM32 烧录 |

### TCP 二进制帧 (OTA 模式)

```
[MAGIC 2B: 0xAA 0x55] [CMD 1B] [SEQ 2B BE] [LEN 2B BE] [PAYLOAD LEN] [CRC16 2B BE]

CMD:
  0x10 OTA_START (S→E)    0x11 OTA_DATA  (S→E)
  0x12 OTA_ABORT (S→E)    0x20 OTA_ACK   (E→S)
  0x21 OTA_DONE  (E→S)    0x22 OTA_ERROR (E→S)
```

同一 TCP 连接复用文本和二进制两种模式，通过 `ota_state` 标志切换，不要在 TCP 上新增第三种通信模式。

## 快速开始

### 环境要求

- Python 3.10+ (服务端)
- MySQL (用户认证数据库)
- ESP32-S3 (硬件端)

### 安装

```powershell
# 创建虚拟环境 (项目根目录)
python -m venv myvenv
.\myvenv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 运行

```powershell
python run.py                  # 默认 0.0.0.0:5000
python run.py --port 8080      # 自定义 HTTP 端口
```

### ESP32 部署

将 `ESP32/main.py` 和 `ESP32/esp32sever/` 目录上传至 ESP32 根目录。修改 `esp32sever/config.py` 中的 WiFi 信息后重启。

```
ESP32/
├── main.py              # 自动启动入口
└── esp32sever/          # 库目录
```

## 功能页面

| 页面 | 路由 | 功能 |
|------|------|------|
| 登录 | `/login` | 用户认证 |
| 主页 | `/` | 状态监控 + 电机控制 |
| 颜色 | `/color` | WS2812 灯带颜色选择 |
| 红外 | `/infrared` | 红外学习 / 发射 |
| 遥控 | `/remote` | 遥控器控制 |
| 上传 | `/upload` | OTA 固件上传 + STM32 烧录 |

## 锁机制

- **`threading.Lock`** — 全局状态互斥 (`_op_lock`)
- **`threading.RLock`** — 红外 JSON 文件读写 (`_ir_json_lock`)，因 `save_ir()` 持锁后内部调用 `read_ir()`
- **`_thread.allocate_lock`** — ESP32 端 `send_lock`，保护两线程共用 `sock.send()`

存在嵌套加锁可能时必须使用 `threading.RLock()`，避免同一线程死锁。

## 协议独占

OTA、Flash 烧录、红外学习、红外发射 **四者互斥**，通过 `try_start_*()` / `end_*()` 原子检查，同时只能进行一项。

## 认证

除 `/login`、`/logout`、`/static/*`、`/socket.io/*` 外，所有路由均需登录。Session cookie 有效期 30 天。

## 数据库

MySQL `127.0.0.1:3306 / lys_test_db`，连接池配置见 `app/utils/db.py`。

## 项目结构

```
esp32_sever/
├── run.py                  # Flask 启动入口
├── requirements.txt        # Python 依赖
├── AGENTS.md               # AI Agent 上下文
├── opencode.json           # opencode 配置
├── app/
│   ├── __init__.py         # Flask 工厂 + 心跳看门狗
│   ├── extensions.py       # SocketIO 扩展
│   ├── events.py           # WebSocket 事件处理
│   ├── ota.py              # OTA 服务端逻辑
│   ├── state.py            # 全局状态
│   ├── tcp.py              # TCP 服务器
│   ├── infrared.json       # 红外数据持久化
│   ├── BIN/                # 固件存储
│   ├── static/             # 静态资源
│   ├── templates/          # Jinja2 模板
│   ├── utils/db.py         # 数据库连接池
│   └── views/              # 蓝图路由
├── ESP32/
│   ├── main.py             # ESP32 主固件 (双线程)
│   └── esp32sever/         # ESP32 库模块
└── myvenv/                 # Python 虚拟环境 (gitignored)
```
