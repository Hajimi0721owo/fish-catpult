# 架构

目标：公开 B 站收藏夹新增普通投稿 → 匿名低画质 MP4 → 用户自己绑定的 QQ 官方机器人 → 群视频。

单用户自部署。持续监控由独立 worker 进程运行，agent 或终端退出不影响监控；日常运行不依赖 agent。设计取舍见 [adr.md](adr.md)。

## 目录职责

| 模块 | 职责 |
| --- | --- |
| `sources/` | 收藏夹全分页快照与匿名视频下载 |
| `vendor/` | 独立打包的匿名下载实现，没有外部 skill 路径依赖 |
| `targets/` | QQ OAuth、分片上传、群消息发送；平台错误码在这里映射成通用异常 |
| `core/pipeline.py` | 首次基线、已见记录、队列推进、下载验证、上传与发送 |
| `core/source_access.py` | 预检与扫描共用的请求预约、冷却和失败退避 |
| `core/control.py` | 页面 / CLI 共用的间隔、自动/手动模式、一次性同步与状态投影 |
| `core/scheduler.py` | 自动周期与显式同步调度；限频、暂停和发送额度在这里生效 |
| `core/delivery_errors.py` | 通用的“明确拒绝 / 结果不明”异常，core 不识别平台品牌 |
| `store.py` | SQLite 单一状态持有者与事务 |
| `config.py` | 本地配置、阈值与校验（间隔 10–86400 秒） |
| `credentials.py` | 只调用系统凭据库，配置保存引用；兼容显式环境变量 |
| `local_files.py` | 私有文件原子写入 |
| `worker.py` / `runtime.py` | 装配适配器与进程锁；后台启动握手、协作停止 |
| `setup/service.py`、`folder_setup.py` | 向导业务：收藏夹预检、凭证验证、绑定、基线、启用 |
| `setup/jobs.py` | 单任务异步执行与脱敏进度，慢接口不阻塞页面 |
| `setup/server.py` | 只监听回环地址；会话令牌、Host/Origin、请求大小与频率限制 |
| `setup/status.py` | 把 Store / Control 状态投影成页面与 `setup-status` 用的非敏感 JSON |
| `setup/ui/` | 中控室页面：静态外壳、步骤视图、角色台词（`lines.js`）、样式与像素角色图 |
| `cli.py` | 命令映射到服务层，输出可供 agent 解释的 JSON |

## 数据流

投递：worker → Scheduler → SourceAccess（预算 / 冷却）→ 收藏夹快照 → Store 原子登记新内容与队列 → 下载与解码验证 → 上传 → 记录 sending → 发消息 → 记录回执或 uncertain / failed。

控制：页面或 agent → CLI / Control → SQLite meta → 唯一 worker 在任务边界读取。控制命令不等待 worker 生命周期锁；当前正在处理的一条允许结束。

部署：浏览器 → 令牌认证的回环 API → SetupService → 配置 / Store / 平台适配器。密钥 POST 后只进入系统凭据库，状态和响应不回显；agent 通过 `setup-status` 读取非敏感进度。用户点击启用后才允许后台转发。

## 不变量

- 首次基线不发送历史；已见 BV 永久保留，移出再加不重发。
- 没有消息回执的发送不自动重试；失败、uncertain 不能经由“立即同步”自动重试。
- 手动模式只停止自动轮询和投递；一次同步只扫描一次，处理该次扫描后冻结的队列。
- 群 OpenID 与机器人 AppID 共同决定目标，禁止把 QQ 群号当 OpenID。
- 不保证发现两次轮询之间加入又删除的视频。

## 选型

标准库 HTTP 服务 + 分文件 HTML/CSS/ES modules：单用户、四步表单，没有 SSR/SEO 需求；相比 React/Vite 不要求用户装 Node，相比桌面 WebView 不增加多平台打包门槛。以后增长到多订阅、历史视图时再评估组件框架。

## 后续扩展

多订阅：增加订阅选择与服务工厂；历史页：独立 UI 模块和只读 API。两者都不改变密钥存储与 HTTP 安全层。
