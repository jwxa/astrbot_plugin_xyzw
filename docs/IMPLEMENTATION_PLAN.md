# XYZW AstrBot 插件实施计划

## 1. 目标与结论

本插件采用 `AstrBot Python 插件 + Node sidecar` 方案。

原因：

- `xyzw_web_helper` 现有价值主要在协议层、命令层、批量任务层。
- 当前 docker 形态本质是静态前端，不适合作为 Python 直接调用的后端服务。
- 将 XYZW 协议执行拆到 Node sidecar，能最大化复用现有 JS 侧协议认知，并保持 AstrBot 插件层职责单一。
- sidecar 不是 AstrBot 独占组件，必须同时可被原 Web 端复用。

## 2. 核心原则

- 用户关系模型固定为 `1 个 QQ 用户 -> 多个 XYZW token`
- 通知策略固定为 `群广播`
- 群聊推送目标必须由用户显式绑定，不能依赖“最后发过言的群”
- 高风险操作默认保留私聊限制
- 插件层不直接实现 XYZW 协议，统一走 sidecar
- sidecar 保持调用方无关：AstrBot 和 Web 共用同一套接口

## 3. 总体架构

```text
QQ / AstrBot                 Web Frontend / Web Admin
    |                                   |
    v                                   v
astrbot_plugin_xyzw              web adapter / web ui
    |- 用户身份识别                        |- 页面交互
    |- 会话控制                            |- 配置管理
    |- 本地存储                            |- 本地展示状态
    |- 通知路由
    |- 定时任务
    \____________________     ____________________/
                         \   /
                          v v
                      XYZW Node sidecar
                        |- token 校验/解析
                        |- BON / WebSocket / 命令执行
                        |- 状态查询
                        |- 日常/副本/资源任务编排
```

## 4. 通知策略

| 场景 | 默认策略 | 说明 |
|---|---|---|
| 群内用户查询/执行 | 当前群回复 | 被动消息 |
| 定时提醒 | 绑定通知群内普通消息 | 主动消息 |
| 执行结果推送 | 绑定通知群内普通消息 | 主动消息 |
| 群推送失败 | 可选降级私聊 | 由配置控制 |
| 敏感操作 | 建议仅私聊 | 绑定、删号、默认账号切换、功法类 |

## 5. 数据模型草案

### 5.1 用户状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `qq_user_id` | string | QQ 用户主键 |
| `accounts` | list | 绑定的 XYZW 账号列表 |
| `default_account_id` | string | 默认账号 |
| `notify.mode` | string | 当前计划为 `group_broadcast` |
| `notify.group.group_id` | string | 默认通知群 |
| `notify.group.unified_msg_origin` | string | 兼容通用主动消息场景 |
| `created_at` | string | 创建时间 |
| `updated_at` | string | 更新时间 |

### 5.2 账号信息

| 字段 | 类型 | 说明 |
|---|---|---|
| `account_id` | string | 插件内部账号 ID |
| `alias` | string | 用户自定义别名 |
| `token` | string | 当前落盘 token；后续建议替换为加密存储 |
| `server_name` | string | 服务器名 |
| `role_name` | string | 角色名 |
| `import_method` | string | `manual` / `url` / `bin` / `wx_qrcode` |
| `source_url` | string | 本地 `refresh_url` 或外部来源地址；用于 token 失效后的自动刷新 |
| `is_default` | boolean | 是否默认账号 |
| `created_at` | string | 创建时间 |

### 5.3 定时任务

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | string | 插件任务 ID |
| `qq_user_id` | string | 所属用户 |
| `job_type` | string | `notify` / `auto_run` |
| `schedule` | string | cron 或日常时间 |
| `account_selector` | object | 单账号 / 全部账号 / 分组 |
| `task_codes` | list | sidecar 任务编码 |
| `enabled` | boolean | 是否启用 |
| `last_run_at` | string | 上次运行时间 |
| `last_status` | string | 最近状态 |

## 6. 功能梳理

### 6.1 账号绑定与基础设施

| 功能 | 状态 | 说明 |
|---|---|---|
| 手动 token 绑定 | 已实现 | 基于 sidecar `/v1/token/verify` 的会话式绑定 |
| URL 绑定 | 已实现基础版 | 插件直连 HTTP/HTTPS URL，兼容顶层原始 token 对象、`token`、`data.token` 三种 JSON 形态，并复用 sidecar 校验 |
| BIN 导入 | 已实现基础版 | 当前支持 BIN 文件或 base64，会话式选择角色并转 token 绑定 |
| 微信扫码绑定 | 已实现基础版 | 二维码私聊下发，`1s` 轮询扫码状态，扫码成功后保存本地 `refresh_url`，待真实联调 |
| 多账号管理 | 已实现 | 列表、重命名、删除 |
| 默认账号切换 | 已实现 | 通过别名或账号 ID 前缀切换 |
| 群通知绑定 | 已实现基础版 | 当前目录已落地最小实现 |
| token 自动刷新 | 已实现基础版 | 每次命令执行前先校验 token；失效时从 `source_url` 获取最新 token 后重试 |

### 6.2 对话类功能

| 功能 | 状态 | 说明 |
|---|---|---|
| 状态总览 | 已实现基础版 | 当前已支持单账号摘要查询 |
| 单账号日常 | 已实现增强版 | 当前为简版日常，已支持账号级配置 `招募次数/挂机领取次数/黑市购买次数/竞技场次数`，并补齐免费钓鱼、黑市与竞技场战斗链路 |
| 车辆操作 | 已实现基础版 | 当前已支持车辆概览、护卫成员状态查询、单车发车和一键收车；发车命令支持可选 `护卫 <护卫ID>`，其中 `品阶 >= 5` 的车辆强制要求护卫，且仅周一至周三 `06:00-20:00` 允许发车 |
| 副本执行 | 已实现基础版 | 当前支持宝库前3、宝库后2、梦境阵容切换、梦境金币商品购买、怪异塔状态、怪异塔免费道具、怪异塔用道具、怪异塔限次爬塔、换皮闯关状态、换皮补打、换皮单 Boss 挑战 |
| 资源执行 | 已实现基础版 | 当前支持招募、免费钓鱼、木箱、珍宝阁免费、黑市采购、军团四圣碎片、每日礼包 |
| 多账号批量 | 计划中 | 次期 |
| 功法类 | 暂缓 | 高风险 |

### 6.3 定时通知/自动执行类功能

| 功能 | 状态 | 说明 |
|---|---|---|
| 挂机提醒 | 已实现基础版 | 当前支持 `/xyzw 定时 挂机 开启|关闭|检查`，检测挂机是否已满并群广播 |
| 收车提醒 | 已实现基础版 | 当前支持 `/xyzw 定时 收车 开启|关闭|检查`，群广播 |
| 活动开放提醒 | 已实现基础版 | 当前支持 `/xyzw 定时 活动 查看|开启|关闭`，首期支持梦境/宝库，按本地时间 `HH:MM` 每日最多提醒一次 |
| 定时日常执行 | 已实现基础版 | 当前支持 `/xyzw 定时 日常 开启|关闭|执行`，按本地时间 `HH:MM` 触发 |
| 定时副本/资源执行 | 已实现基础版 | 当前支持 `/xyzw 定时 资源|副本 查看|开启|关闭|执行`，复用现有 sidecar 动作并按 `HH:MM` 每日触发 |
| 执行结果汇总 | 已实现基础版 | 定时日常、定时资源、定时副本执行后会向绑定群广播结果摘要 |

## 7. sidecar API 草案

### 7.1 最小接口集

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | sidecar 健康检查 |
| `GET` | `/v1/token/source/:source_id` | 从已登记的 token source 获取最新 token |
| `POST` | `/v1/token/source/register-bin` | 登记 bin / 微信扫码来源，生成本地 `refresh_url` |
| `POST` | `/v1/token/wechat-qrcode/start` | 获取微信扫码二维码 |
| `POST` | `/v1/token/wechat-qrcode/status` | 查询微信扫码状态 |
| `POST` | `/v1/token/wechat-qrcode/consume` | 消费扫码结果，返回角色列表与 token source |
| `POST` | `/v1/token/server-list` | 从 bin 获取角色列表 |
| `POST` | `/v1/token/authuser` | bin 转 WebSocket-ready token |
| `POST` | `/v1/token/verify` | 校验 token 并返回角色摘要 |
| `POST` | `/v1/account/describe` | 获取角色/服务器/基础状态 |
| `POST` | `/v1/command/run` | 执行单个命令 |
| `POST` | `/v1/car/overview` | 获取车辆概览 |
| `POST` | `/v1/car/helpers` | 获取疯狂赛车护卫成员状态与 `memberHelpingCntMap` |
| `POST` | `/v1/car/send` | 执行单车发车，支持可选 `helper_id` |
| `POST` | `/v1/car/claim-ready` | 一键收取已完成车辆 |
| `POST` | `/v1/task/run-daily` | 执行单账号日常 |
| `POST` | `/v1/dungeon/run` | 执行单账号副本动作 |
| `POST` | `/v1/resource/run` | 执行单账号资源动作 |
| `POST` | `/v1/task/run-batch` | 执行批量任务 |
| `POST` | `/v1/notify/snapshot` | 拉取用于提醒的状态快照 |

### 7.2 请求示例

```json
{
  "user_id": "123456",
  "account_id": "acc_main",
  "token": "encrypted-or-plaintext-for-sidecar",
  "command": "role_getroleinfo",
  "params": {}
}
```

### 7.3 响应示例

```json
{
  "ok": true,
  "code": "OK",
  "message": "success",
  "data": {
    "role_name": "主号",
    "server_name": "S1",
    "summary": {}
  }
}
```

## 8. 命令规划

| 命令 | 用途 | 阶段 |
|---|---|---|
| `/xyzw` | 帮助入口 | 已实现 |
| `/xyzw 健康` | 查看 sidecar 健康状态 | M1 |
| `/xyzw 绑定` | 会话式绑定账号 | 已实现（当前支持 `token` / `url` / `bin` / `wx`） |
| `/xyzw 账号` | 列表、切换、重命名、删除 | 已实现 |
| `/xyzw 状态` | 查看摘要状态 | 已实现基础版 |
| `/xyzw 车` | 查看车辆概览/护卫成员状态/执行发车/收车 | 已实现基础版（支持 `/xyzw 车 护卫成员 [成员ID或名称关键字] [别名或ID前缀]`；发车支持可选护卫参数，高品级强制护卫） |
| `/xyzw 日常` | 执行常用日常与维护账号级日常配置 | 已实现增强版（支持 `/xyzw 日常 配置 查看|设置|重置`，当前可维护招募/挂机/黑市/竞技场次数） |
| `/xyzw 资源` | 招募、钓鱼、开箱等资源动作 | 已实现基础版 |
| `/xyzw 副本` | 宝库、梦境、怪异塔 | 已实现基础版 |
| `/xyzw 通知 ...` | 绑定群、查看、测试 | M0 |
| `/xyzw 定时 ...` | 任务增删改查 | 已实现基础版（当前支持收车提醒、挂机提醒、定时日常、活动开放提醒） |

## 9. 里程碑

| 里程碑 | 范围 | 当前状态 |
|---|---|---|
| `M0` | 插件目录、配置、通知群最小能力、计划文档 | 已完成 |
| `M1` | sidecar 最小接口、手动绑定、账号管理、状态查询 | 进行中（已完成核心闭环，并补齐微信扫码绑定与 token 自动刷新基础版） |
| `M2` | 日常执行、车辆操作、基础副本/资源命令 | 进行中（已落地简版日常、车辆基础版、副本基础版含梦境金币商品购买、怪异塔用道具/限次爬塔与换皮补打、资源基础版含黑市采购与军团四圣碎片） |
| `M3` | 定时提醒、定时日常、执行报告 | 进行中（已落地收车提醒基础版、挂机提醒基础版、定时日常基础版、定时资源/副本执行基础版与活动开放提醒基础版） |
| `M4` | 批量执行、更多高级任务 | 待做 |

## 10. 风险与约束

| 项目 | 风险 | 处理策略 |
|---|---|---|
| token 安全 | 明文落盘风险 | 至少做加密存储和日志脱敏 |
| 群通知误投 | 推送到错误群 | 必须显式绑定通知群 |
| sidecar 可用性 | sidecar 宕机导致功能不可用 | 做健康检查和错误回传 |
| Web 暴露面 | 浏览器跨域和公网暴露风险 | 支持 CORS，但建议放反向代理后并限制来源 |
| 高风险命令 | 功法、密码类操作误执行 | 默认私聊限制 + 二次确认 |
| 并发执行 | 多账号并发导致限流/封控 | 在 sidecar 侧统一限流 |

## 11. 当前目录已落地内容

- 插件主入口：`main.py`
- 本地存储：`storage.py`
- 通知路由：`notifier.py`
- sidecar 客户端：`sidecar_client.py`
- 已落地命令：`/xyzw 健康`、`/xyzw 绑定`、`/xyzw 绑定 url`、`/xyzw 绑定 bin`、`/xyzw 账号`、`/xyzw 状态`、`/xyzw 车`、`/xyzw 日常`、`/xyzw 副本`、`/xyzw 资源`、`/xyzw 通知 ...`、`/xyzw 定时 ...`
- sidecar 服务最小实现：`sidecar/`
- 配置 schema：`_conf_schema.json`
- 项目说明：`README.md`

## 12. 当前 sidecar 范围边界

- 已实现：`/health`、`/v1/token/server-list`、`/v1/token/authuser`、`/v1/token/verify`、`/v1/account/describe`、`/v1/command/run`、`/v1/car/overview`、`/v1/car/helpers`、`/v1/car/send`、`/v1/car/claim-ready`、`/v1/task/run-daily`、`/v1/dungeon/run`、`/v1/resource/run`
- 当前输入支持：WebSocket-ready token、原始 `bin` 的 base64（插件侧另外支持 BIN 文件上传）
- 已补基础 Web 复用能力：`OPTIONS` 预检、CORS、可配置监听 host
- 插件侧已实现 URL 绑定：插件直接拉取 HTTP/HTTPS JSON，并兼容顶层原始 token 对象、`token`、`data.token`，随后调用 `/v1/token/verify`
- 未实现：sidecar 侧 URL 拉 token、批量任务、复杂副本/资源任务

这份文档的目标不是描述最终实现细节，而是冻结当前阶段的范围、职责边界和实施顺序，供后续按里程碑推进。
