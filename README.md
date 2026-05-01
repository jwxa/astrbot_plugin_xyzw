# astrbot_plugin_xyzw

面向 AstrBot 的《咸鱼之王》助手插件，采用 `AstrBot Python 插件 + Node sidecar` 架构。

## 定位

- 插件侧负责 QQ 会话、账号绑定、多账号管理、通知路由与定时任务。
- sidecar 负责 XYZW 协议、token 校验与刷新、命令执行、日常/资源/副本编排。
- sidecar 同时服务于 AstrBot 与 `xyzw_web_helper`，不是插件私有实现。

## 当前能力

- 账号绑定：`token / url / bin / wx`
- 多账号管理：列表、默认账号、重命名、删除
- 状态/车辆：状态摘要、车辆概览、护卫成员、发车、收车
- 日常：简版日常、账号级日常配置、异步结果回发
- 资源：招募、钓鱼、开箱、珍宝阁、黑市、四圣碎片、每日礼包
- 副本：宝库、梦境、怪异塔、换皮闯关
- 通知与定时：群广播、收车提醒、挂机提醒、护卫成员提醒、定时日常、定时资源/副本执行、活动开放提醒
- token 刷新：`refresh_url` / `token source` 自动刷新链路

## 目录结构

```text
astrbot_plugin_xyzw/
├── __init__.py
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── notifier.py
├── sidecar_client.py
├── storage.py
├── sidecar/
│   ├── package.json
│   ├── README.md
│   ├── src/
│   └── vendor/
├── docs/
│   └── IMPLEMENTATION_PLAN.md
└── _refs/
```

## 运行依赖

- [AstrBot](./_refs/AstrBot)
- [xyzw_web_helper](./_refs/xyzw_web_helper)

获取仓库后建议执行：

```bash
git submodule update --init --recursive
```

## 启动说明

### 1. AstrBot 插件

将本目录放入 AstrBot 插件目录后热加载，按 `_conf_schema.json` 配置 `sidecar_base_url`。

### 2. sidecar

```bash
cd sidecar
npm install
node ./src/server.js
```

默认监听 `127.0.0.1:8099`。如果需要给 AstrBot 容器或 Web 容器访问，请显式设置：

```bash
XYZW_SIDECAR_HOST=0.0.0.0
XYZW_SIDECAR_PORT=8099
```

## 文档

- 实施计划见 [docs/IMPLEMENTATION_PLAN.md](./docs/IMPLEMENTATION_PLAN.md)
- sidecar 接口说明见 [sidecar/README.md](./sidecar/README.md)

## 脱敏说明

以下内容不进入 git：

- `sidecar/data/` 下 token source / 微信扫码会话落盘
- `sidecar/*.log` 本地日志
- `docs/VERIFICATION_STATUS.md` 这类包含本地联调账号、群号、时间线的私有记录

## 状态

当前仓库基于本地已验证实现整理而来；源实现目录不带 git 历史，因此本仓库以首次提交作为公开起点。
