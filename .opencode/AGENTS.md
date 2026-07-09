# AGENTS.md — ESP32 Sever

## 启动命令

```powershell
.\myvenv\Scripts\Activate.ps1   # 虚拟环境在 .\myvenv（gitignored）
python run.py                    # 默认 0.0.0.0:5000
python run.py --port 8080        # 自定义端口
```

依赖安装：`pip install -r requirements.txt`（flask>=3.0, flask-socketio>=5.0, pymysql, DBUtils）

## 架构

- **`app/`** — Flask + SocketIO 服务端。两个端口：
  - **5000**: HTTP + WebSocket（浏览器 ↔ 服务端），SocketIO async_mode=`threading`
  - **9000**: 原始 TCP（服务端 ↔ ESP32），同一连接复用文本协议和 OTA 二进制帧
- **`ESP32/`** — MicroPython 代码，运行在 ESP32 硬件上。**不是本地可运行的**，不要修改后尝试本地执行。
- 入口：`run.py` → `app.create_app()` → Flask app factory + 启动 TCP server + heartbeat watchdog 守护线程

## 关键约定

- **全局共享状态**在 `app/state.py`，跨线程访问。操作互斥用 `threading.Lock`（`_op_lock`），红外 JSON 文件用 `threading.RLock()`（`_ir_json_lock`），因为 `save_ir()` 持锁后内部调用 `read_ir()`。
- **加锁规范**：存在嵌套加锁可能时必须使用 `threading.RLock()`，避免同一线程死锁。
- **TCP 协议复用**：同一 TCP 连接复用文本协议（`\n` 分隔）和 OTA 二进制帧（`0xAA 0x55` 魔术头），由 `st.ota_active` 标志切换，不要在 TCP 连接上新增第三种通信模式。
- **OTA 独占**：OTA/Flash/红外学习/红外发射四者互斥，通过 `try_start_*()` / `end_*()` 原子检查。
- **认证**：`app/__init__.py:auth()` — 除 `/login`, `/logout`, `/static/*`, `/socket.io/*` 外全部需要登录。
- **数据库**：MySQL `127.0.0.1:3306 / lys_test_db`，连接池在 `app/utils/db.py` (PooledDB)。
- **固件文件**：OTA 固件固定保存到 `app/BIN/template.bin`（最大 1MB），通过 `/upload` 页面上传。

## 无测试

项目无 pytest/unittest 测试文件，修改后手动启动服务验证。

## LSP

Pyright 已配置，LSP 命令位于 `.opencode/opencode.json`（Windows 绝对路径指向 myvenv 内的 pyright-langserver）。
