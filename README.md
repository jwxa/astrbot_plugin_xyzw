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
- 周/月度专项定时：每周答题、月末月度任务提醒、月末钓鱼进度补齐、疯狂赛车禁发提醒、疯狂赛车智能发车、疯狂赛车夜间收车提醒、疯狂赛车主动收车
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

配置说明：

- `request_timeout` 为统一的 sidecar 调用超时（秒）；收车提醒、挂机提醒、手动智能发车、定时日常都会在此基础上换算具体请求超时。
- `notify_mode` 为通知策略：
  - `group_broadcast`：优先发到已绑定通知群
  - `private_only`：直接私聊当前用户，适合测试验证时避免打扰群组

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

## 定时专项能力

新增的专项定时任务全部走 `/xyzw 定时`：

```text
/xyzw 定时 答题 查看
/xyzw 定时 答题 开启 [周几] <HH:MM> [别名或ID前缀]
/xyzw 定时 答题 关闭 [别名或ID前缀]
/xyzw 定时 答题 执行 [别名或ID前缀]

/xyzw 定时 月度 查看
/xyzw 定时 月度 提醒 开启 <HH:MM> [别名或ID前缀]
/xyzw 定时 月度 提醒 关闭 [别名或ID前缀]
/xyzw 定时 月度 提醒 执行 [别名或ID前缀]
/xyzw 定时 月度 钓鱼 开启 <HH:MM> [别名或ID前缀]
/xyzw 定时 月度 钓鱼 关闭 [别名或ID前缀]
/xyzw 定时 月度 钓鱼 执行 [别名或ID前缀]

/xyzw 定时 赛车 查看
/xyzw 定时 赛车 提醒 开启 <HH:MM> [别名或ID前缀]
/xyzw 定时 赛车 提醒 关闭 [别名或ID前缀]
/xyzw 定时 赛车 提醒 执行 [别名或ID前缀]
/xyzw 定时 赛车 智能发车 开启 <HH:MM> [别名或ID前缀]
/xyzw 定时 赛车 智能发车 关闭 [别名或ID前缀]
/xyzw 定时 赛车 智能发车 执行 [别名或ID前缀]
/xyzw 定时 赛车 收车提醒 开启 [HH:MM] [别名或ID前缀]
/xyzw 定时 赛车 收车提醒 关闭 [别名或ID前缀]
/xyzw 定时 赛车 收车提醒 执行 [别名或ID前缀]
/xyzw 定时 赛车 主动收车 开启 [HH:MM] [别名或ID前缀]
/xyzw 定时 赛车 主动收车 关闭 [别名或ID前缀]
/xyzw 定时 赛车 主动收车 执行 [别名或ID前缀]
```

规则说明：

- 答题按每周一次执行，若未提供周几则默认周一。
- 月度提醒与钓鱼补齐固定在月末触发。
- 疯狂赛车“提醒”指的是禁发提醒：在周一至周三的发车窗口结束前提醒群内“20:00 后将无法继续发车”。
- 疯狂赛车禁发提醒与智能发车固定在周一至周三生效，且时间必须位于 `06:00-20:00`。
- 疯狂赛车收车提醒与主动收车默认为每日 `23:55`，也支持自定义每日时间。

## 手动智能发车

手动触发命令：

```text
/xyzw 赛车 智能发车 [别名或ID前缀]
/xyzw 赛车 智能发车 白名单 查看 [别名或ID前缀]
/xyzw 赛车 智能发车 白名单 设置 <成员ID1,成员ID2,...> [别名或ID前缀]
/xyzw 赛车 智能发车 白名单 清空 [别名或ID前缀]
```

当前手动智能发车为独立 sidecar 逻辑，和定时“赛车 智能发车”不是完全同一套流程。

特点：

- 同步执行，命令返回后直接给出最多 4 辆车的处理明细
- 会同时拉取车辆、角色、护卫成员状态
- 优先使用当前账号的护卫白名单
- 白名单成员都满员时，随机选择一个可用护卫
- 每辆车最多刷新 5 次
- 单车刷新或发车失败会自动重试 1 次

说明：

- `/xyzw 赛车 智能发车` 与 `定时 赛车 智能发车` 现在都统一走独立 sidecar 逻辑
- 旧的 `/v1/car/smart-send` 仅保留兼容，不再作为推荐入口
- 护卫白名单按账号隔离，通过 `/xyzw 赛车 智能发车 白名单 ...` 管理
- 兼容旧前缀：`/xyzw 车 ...` 仍然可用

## 脱敏说明

以下内容不进入 git：

- `sidecar/data/` 下 token source / 微信扫码会话落盘
- `sidecar/*.log` 本地日志
- `docs/VERIFICATION_STATUS.md` 这类包含本地联调账号、群号、时间线的私有记录

## 状态

当前仓库基于本地已验证实现整理而来；源实现目录不带 git 历史，因此本仓库以首次提交作为公开起点。
