# XYZW Node Sidecar

这是 `astrbot_plugin_xyzw` 的最小 Node sidecar。

它的定位不是“只给 AstrBot 用的内部进程”，而是当前架构中的共享能力层：

- AstrBot 插件可以调用
- 原 Web 端也可以调用

因此接口设计保持 HTTP 化、调用方无关。

## 当前已实现接口

- `GET /health`
- `GET /v1/token/source/:source_id`
- `POST /v1/token/source/register-bin`
- `POST /v1/token/wechat-qrcode/start`
- `POST /v1/token/wechat-qrcode/status`
- `POST /v1/token/wechat-qrcode/consume`
- `POST /v1/token/server-list`
- `POST /v1/token/authuser`
- `POST /v1/token/verify`
- `POST /v1/account/describe`
- `POST /v1/command/run`
- `POST /v1/car/overview`
- `POST /v1/car/helpers`
- `POST /v1/car/send`
- `POST /v1/car/claim-ready`
- `POST /v1/task/run-daily`
- `POST /v1/dungeon/run`
- `POST /v1/resource/run`

## 当前输入限制

当前版本支持两类输入：

1. **WebSocket-ready token 字符串**
2. **原始 bin 的 base64 字符串**

WebSocket-ready token 可以直接用于：

```text
wss://xxz-xyzw.hortorgames.com/agent?p=<token>&e=x&lang=chinese
```

可接受的典型输入：

- 直接传入 JSON token 字符串
- base64 包装后的 JSON token 字符串
- 包含 `token` / `gameToken` 字段的 JSON 包装对象

当前额外已支持：

- 保存 bin / 微信扫码来源并生成本地 `refresh_url`
- 通过 `GET /v1/token/source/:source_id` 动态重建最新 token
- AstrBot 与原 Web 共用同一套 token 刷新入口

当前**未实现**：

- 批量任务接口
- 复杂副本/资源批处理接口

## 启动方式

```bash
cd /tools/Code/AstrBot/astrbot_plugin_xyzw/sidecar
npm install
npm run start
```

默认监听：

```text
http://127.0.0.1:8099
```

也可以通过环境变量覆盖：

```bash
PORT=8099 npm run start
```

如果要让原 Web 前端跨域调用，建议至少配置：

```bash
XYZW_SIDECAR_HOST=0.0.0.0
XYZW_SIDECAR_ALLOW_ORIGIN=http://your-web-host:port
npm run start
```

当前已补：

- `OPTIONS` 预检
- `Access-Control-Allow-Origin`
- `Access-Control-Allow-Methods`
- `Access-Control-Allow-Headers`

说明：

- AstrBot 本地调用时，默认 `127.0.0.1` 足够
- 浏览器直连时，建议放在反向代理后，而不是直接裸露公网

## 请求示例

### 健康检查

```bash
curl "http://127.0.0.1:8099/health"
```

### token 校验

```bash
curl -X POST "http://127.0.0.1:8099/v1/token/verify" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}"
  }'
```

### 登记 token source

```bash
curl -X POST "http://127.0.0.1:8099/v1/token/source/register-bin" \
  -H "Content-Type: application/json" \
  -d '{
    "bin_base64": "<base64-bin>",
    "source_type": "bin"
  }'
```

### 通过 refresh_url 获取最新 token

```bash
curl "http://127.0.0.1:8099/v1/token/source/<source_id>?server_id=123456"
```

### 获取微信扫码二维码

```bash
curl -X POST "http://127.0.0.1:8099/v1/token/wechat-qrcode/start" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 获取 bin 角色列表

```bash
curl -X POST "http://127.0.0.1:8099/v1/token/server-list" \
  -H "Content-Type: application/json" \
  -d '{
    "bin_base64": "<base64-bin>"
  }'
```

### bin 转 authuser token

```bash
curl -X POST "http://127.0.0.1:8099/v1/token/authuser" \
  -H "Content-Type: application/json" \
  -d '{
    "bin_base64": "<base64-bin>",
    "server_id": 123456
  }'
```

### 账号描述

```bash
curl -X POST "http://127.0.0.1:8099/v1/account/describe" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}"
  }'
```

### 执行单个命令

```bash
curl -X POST "http://127.0.0.1:8099/v1/command/run" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}",
    "command": "role_getroleinfo",
    "params": {}
  }'
```

### 获取车辆概览

```bash
curl -X POST "http://127.0.0.1:8099/v1/car/overview" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}"
  }'
```

### 获取护卫成员状态

```bash
curl -X POST "http://127.0.0.1:8099/v1/car/helpers" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}",
    "keyword": "张三"
  }'
```

### 单车发车

```bash
curl -X POST "http://127.0.0.1:8099/v1/car/send" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}",
    "car_id": "10001",
    "helper_id": 0,
    "text": "",
    "is_upgrade": false
  }'
```

### 一键收取可收车辆

```bash
curl -X POST "http://127.0.0.1:8099/v1/car/claim-ready" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}"
  }'
```

### 执行简版基础日常

```bash
curl -X POST "http://127.0.0.1:8099/v1/task/run-daily" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}",
    "options": {
      "recruitCount": 2,
      "hangUpClaimCount": 5,
      "blackMarketPurchaseCount": 1,
      "arenaBattleCount": 3
    }
  }'
```

### 执行资源动作

```bash
curl -X POST "http://127.0.0.1:8099/v1/resource/run" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}",
    "action": "recruit_free"
  }'
```

### 执行副本动作

```bash
curl -X POST "http://127.0.0.1:8099/v1/dungeon/run" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "{\"roleToken\":\"***\",\"sessId\":1,\"connId\":1,\"isRestore\":0}",
    "action": "bosstower_low"
  }'
```

## 返回说明

- `/v1/token/server-list`：返回角色列表、原始 `rawRoles` 和解析后的 `binPayload`
- `/v1/token/authuser`：返回可直接用于后续校验和连接的 `wsReadyToken`
- `/v1/token/verify`：返回精简摘要，适合绑定前校验
- `/v1/account/describe`：返回 token 元信息、摘要和完整 `roleInfo`
- `/v1/command/run`：返回单次命令执行结果，主体为解码后的 `body`
- `/v1/car/overview`：返回车辆汇总和标准化车辆列表
- `/v1/car/helpers`：返回护卫成员列表、成员护卫次数映射 `memberHelpingCntMap`，以及按红粹排序后的成员状态
- `/v1/car/send`：返回单车发车结果以及发车前后概览
- `/v1/car/claim-ready`：返回本次收车结果以及收车前后概览
- `/v1/task/run-daily`：返回简版基础日常的步骤执行汇总；支持通过 `options.recruitCount`、`options.hangUpClaimCount`、`options.blackMarketPurchaseCount`、`options.arenaBattleCount` 覆盖账号级执行次数；免费钓鱼在检测到当日可领取时默认执行 `3` 次
- `/v1/dungeon/run`：返回单个副本动作的执行结果，当前支持宝库、梦境阵容/金币商品购买、怪异塔、换皮闯关基础动作、换皮补打与怪异塔用道具
- `/v1/resource/run`：返回单个资源动作的执行结果，当前支持招募、钓鱼、开箱、珍宝阁免费、黑市采购、军团四圣碎片、每日礼包

## 实现说明

- BON 协议实现当前 vendored 自 `xyzw_web_helper`
- WebSocket 客户端为 sidecar 内部最小实现
- 本版本目标是先打通“本地 sidecar 可启动 + AstrBot / Web 都可调用”的最短路径
