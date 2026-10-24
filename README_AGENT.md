# Fish-CatPult（鱼猫投石机）· Agent 运行手册

> 本文件写给部署、配置和日常控制本项目的 LLM / coding agent：只写确定的命令、状态和约束。
> 人类读者请看展厅版 [README.md](README.md)。产品名为 Fish-CatPult；Python 包、配置与命令仍叫 `bili-fav-relay`（`fish-catpult` 是等价别名）。

## 1. 系统不变量

- **运行环境**：Python 3.11+，可执行的 `ffmpeg`、`ffprobe`（需支持 `-enc_time_base:v demux`）。运行时不需要 agent、模型 API、B 站 Cookie。
- **凭据**：只从本地向导写入的系统凭据库（macOS 钥匙串 / Windows 凭据管理器 / Linux Secret Service）或用户明确设置的环境变量读取。`config.json` 只存随机引用。不要让用户把 AppSecret 发到聊天里，不读取其他项目的凭据。
- **只读命令**：`status`、`setup-status`、不带参数的 `schedule` 不发外部请求，不加载密钥，可随时调用。
- **状态**：唯一状态源是该订阅的 SQLite Store。禁止直接改 SQLite、删除已见 BV 的去重记录、或用重启 / 清库绕过请求冷却与失败退避。
- **授权**：用户举例或讨论功能不等于授权现在同步。真正向群发送需要用户明确目标群并授权；不要自动改 `send_enabled`，不要自动 `resume`。
- **多订阅**：已知配置路径时，所有命令都带 `--config /绝对路径/config.json`（放在子命令前）。

## 2. 用户意图 → 命令

下表省略 `--config`。完整话术与结果解读见 [docs/agent-usage.md](docs/agent-usage.md)，首次部署见 [docs/deployment.md](docs/deployment.md)。

| 用户意图 | 命令 | 约束与副作用 |
| --- | --- | --- |
| 帮我部署 / 打开向导 | `setup` | 打开本机回环地址的向导；链接只在运行程序的电脑上有效，不要公开分享。`setup --no-open` 只打印链接 |
| 部署到哪一步了 | `setup-status` | 只读 |
| 每 X 分钟同步 | `schedule --minutes X` | 只改间隔，不改模式 |
| 每 X 秒同步 | `schedule --seconds X` | 10–86400 整数秒，与 `--minutes` 互斥。10 秒是下限，不承诺安全或准时送达 |
| 改成手动同步 | `schedule --mode manual` | 停止后续自动处理，保留队列，不中断当前一条 |
| 现在同步一次 | `sync` | 只本次扫描与投递，不切回自动模式，不补发历史 |
| 取消这次同步 | `sync --cancel` | 当前一条可结束，保留待发队列 |
| 启动后台 | `start` | 独立后台进程，重复启动会复用现有进程 |
| 同步到哪了 | `status` | 只读；用 `interval_seconds`、`control.sync`、计数回答 |
| 停止后台 | `stop` | 当前一条完成后退出；再用 `status` 确认已停止 |

## 3. 投递状态机与恢复

```
queued ──▶ preparing（下载、完整解码、上传）──▶ sent（QQ 返回消息 ID）
   │
   ├──▶ failed（下载/上传失败，或明确的发送权限拒绝）──▶ 处理原因后 retry <BV>
   │
   └──▶ uncertain（超时、缺回执、未知拒绝、5xx、发送阶段崩溃）──▶ 用户核对群后 resolve
```

- **`40034105`（QQ 拒绝主动消息）**：任务为 `failed`，`status` 的 `control.delivery_problem` 带数字码。让用户在目标群的机器人设置里开启“主动发言”（必要时请群主或管理员）。不要反复 `sync` 试探。用户确认开启并授权重试后：`stop` → `status` 确认已停 → `retry <BV>` → `resume` → `sync`，需要持续运行再 `start`。
- **`uncertain`**：把 BV 号给用户，请他看群。已收到 → `resolve <BV> --delivered yes`；确认没收到 → `resolve <BV> --delivered no`。禁止自动重试导致重复发送。
- **退避**：B 站访问失败按不低于 5 分钟的基数指数退避（首次至少 10 分钟），上限一天；风控暂停需用户确认原因已处理后再恢复。

## 4. 边界参数

| 项 | 值 |
| --- | --- |
| 视频时长上限 | 30 分钟（`max_duration_seconds`） |
| MP4 体积上限 | 30,000,000 字节，超出直接失败，不自动压缩 |
| 清晰度 | 请求 `qn=16`（360P），实际以下载校验为准 |
| 多 P 投稿 | 默认拒绝；`multipart_policy: "first"` 才只发 P1 |
| 自动轮询间隔 | 10–86400 秒，默认 900 秒 |
| 手动批次投递间隔 | 默认至少 10 秒（`delivery_gap_seconds`） |
| 媒体缓存 | 默认 1GB，超额暂停下载 |

---

以下为完整的安装、配置与运维说明。

## 安装

**极速部署**：在你的 agent 里发送：

> 帮我部署 https://github.com/Hajimi0721owo/fish-catpult ，按仓库里的 README_AGENT.md 完成安装，并引导我打开本地设置向导。

当前仓库为私密仓库，agent 使用的 GitHub 账号需要有访问权限。机器人密钥在本地设置页填写，不用发到聊天里。

也可以手动安装：

需要 Python 3.11+ 和可执行的 `ffmpeg`、`ffprobe`（较新 FFmpeg，需支持 `-enc_time_base:v demux`）。macOS 可用 `brew install ffmpeg`，其他系统从 FFmpeg 官方发行渠道安装并加入 PATH。

下载仓库后，在项目目录执行：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
bili-fav-relay setup
```

Windows 激活命令为 `.venv\Scripts\Activate.ps1`。安装依赖为 `websockets` 与 `keyring`；无需 Node.js、Chromium 或其他项目目录。浏览器只打开本地回环地址，设置链接仅供本人使用，不要公开分享。

## 推荐：跟随本地向导

1. **选收藏夹**：粘贴公开收藏夹链接，也可以整段粘贴手机 App“分享 → 复制链接”的文字（含 b23.tv 短链）。页面预检标题与数量，不建立基线；短链会先解析跳转。预检也计入请求冷却，私密或超过 1000 条会直接说明原因。
2. **连接机器人**：到 [QQ 开放平台](https://q.qq.com/)创建或选用自己的机器人（页面内有 5 步指引：AppID/AppSecret、IP 白名单、沙箱群、加群）；在本地页面填写 AppID、AppSecret，验证后保存到系统凭据库。IP 不在白名单（11298）等常见错误会直接给出处理办法。
3. **绑定 QQ 群**：先在目标 QQ 群的机器人设置中开启**主动发言**权限；没有操作权限时请联系群主或管理员。再点击生成口令，在目标群 @机器人发送页面上的“绑定 六位数字”。口令出现前不要先发；页面显示倒计时，约 5 分钟过期后可重新生成。
4. **开始转发**：记下已有收藏建立基线，选择手动或自动，再点击“开始转发”。此前不会发送视频。

预检和读取已有收藏至少间隔 10 秒，失败时保留更长退避；页面显示下次可读取时间。首次配置遇到风控会暂停，冷却结束且确认原因已处理后，可在页面点击“恢复访问”，再手动继续。重开页面、换收藏夹不会重置冷却。候选收藏夹验证失败时保留原配置、群绑定与历史记录。

向导默认建议手动同步。启用后关闭页面或终端不影响独立后台进程；电脑关机/重启后需再次启动。首次可新增一条普通短视频，再点“立即同步”验证，仍遵守最低 10 秒收藏夹请求间隔。

macOS 使用钥匙串，Windows 使用系统凭据管理器，Linux 桌面使用 Secret Service。配置文件只保存随机凭据引用，密钥不回显、不写日志；系统凭据库不可用时明确报错，不回退为明文文件。无桌面服务器可使用下方环境变量方式。

```sh
bili-fav-relay setup-status  # agent 查看部署进度，无外部请求，不加载密钥
bili-fav-relay start         # 启动独立后台进程，自动避免重复启动
bili-fav-relay status        # 确认实际进程、模式与上次调度结果
bili-fav-relay stop          # 当前一条完成后退出；用 status 确认已停止
```

已知配置路径时始终带上 `--config /绝对路径/config.json`，放在子命令前。完整 agent 引导见 [docs/deployment.md](docs/deployment.md)。`setup --no-open` 只输出本地设置链接；该地址必须在运行程序的电脑上打开。

## 命令行配置（无桌面环境）

先运行 `bili-fav-relay init`，再修改生成的 `config.json`：


```json
{
  "data_dir": "data",
  "folder": "你的公开收藏夹ID",
  "poll_seconds": 900,
  "send_enabled": false
}
```

`folder` 也接受带 `fid=` 的 B 站收藏夹完整链接。每个配置文件对应一个收藏夹和一个目标群；第二条订阅使用另一份配置和独立 `data_dir`。路径相对配置文件所在目录解析。

## 机器人和群绑定

本地向导已保存凭据时可跳过本节。无桌面环境中，在 QQ 开放平台创建自己的机器人，完成所需权限并将机器人加入目标群，将凭据通过安全的运行环境提供，不要提交到 GitHub。下面仅说明变量名，真实 AppSecret 推荐用终端隐藏输入，避免写进命令历史：

```sh
export BILI_RELAY_QQ_APP_ID='你的 AppID'
export BILI_RELAY_QQ_APP_SECRET='你的 AppSecret'
bili-fav-relay doctor
bili-fav-relay bind --alias '视频分享群'
```

`bind` 在完成机器人鉴权、网关 READY 后显示一次性口令。你在目标群 **@机器人 发送“绑定 六位数字”**，程序从收到的群事件保存 `group_openid`。它不会自动向群发送测试消息。QQ 群号不能代替 `group_openid`；绑定结果同时锁定 AppID。更换机器人或目标群请新建独立数据目录，避免旧队列投错群。

身份验证、群绑定和上传成功都不等于群已允许主动发视频。群内的“主动发言”开关需要单独开启；若仍被平台拒绝，以错误码和实际权限为准。

这里使用腾讯官方机器人，不需要 NapCat、个人 QQ 扫码或 OneBot。QQ 新旧官方文档对主动消息权限描述不一致，当前机器人能否主动发送需真实联调；被平台拒绝会暂停，绝不伪造消息 ID 模拟被动回复。

## 先建基线，再开启转发

```sh
# 可选：只读取前 3 条，单次请求；不建立基线、不下载、不发送
bili-fav-relay inspect

# 首次完整扫描只登记历史，默认最多 5 页、每页 20 条
bili-fav-relay scan
bili-fav-relay status
```

`inspect` 和 `scan` 共用持久化冷却时间。运行一次后需要等到下一次轮询时间，不提供绕过限频的 `--force`。显式 `sync` 可提前普通自动计划，但距离上次请求仍至少 10 秒，失败退避不缩短。如果收藏夹超过 100 条，首次扫描会明确暂停；按实际大小提高 `max_pages`（最多 50），然后 `resume`。半份列表不会成为基线。

基线成功之后，将 `config.json` 的 `send_enabled` 改为 `true`，再启动：

```sh
# 执行一次调度；手动模式下不会自动扫描或投递
bili-fav-relay run --once

# 持续运行；终端关闭则停止。长期部署用系统服务管理器保持此进程运行。
bili-fav-relay run
```

只有运行中的进程才会监控。推荐 `start` 启动独立后台进程；没有自动安装开机服务，重启后可再次 `start`，或按部署指南使用系统服务管理器。不要同时运行两个 worker。

自动模式每轮默认最多发 1 条视频，队列依收藏时间处理。`scan` 永远不发送，`run` 只有在 `send_enabled=true` 且绑定有效时才上传和发送。默认无额外文字消息，避免一条视频产生多次通知。

## 与 agent 对话控制

用户可以说“改成每 10 分钟同步”“切换为手动同步”“现在同步新增收藏”。agent 调用以下命令即可，不需要读取机器人密钥：

```sh
bili-fav-relay schedule                         # 查看模式和间隔，离线
bili-fav-relay schedule --minutes 10            # 修改自动间隔，不改变模式
bili-fav-relay schedule --seconds 10            # 最短每 10 秒一轮，不改变模式
bili-fav-relay schedule --mode manual           # 停止后续自动扫描、自动发送
bili-fav-relay sync                             # 本次扫描并处理待发视频
bili-fav-relay status                           # 查看排队、冷却、完成与失败
bili-fav-relay sync --cancel                    # 当前一条结束后取消本次任务
bili-fav-relay schedule --mode auto             # 恢复自动模式
```

页面可输入整数数值并选择秒或分钟，范围为 10 秒至 24 小时；CLI 的 `--seconds` 和 `--minutes` 二选一。只改模式会保留间隔。状态中的 `interval_seconds` 是精确秒值，`interval_minutes` 保留兼容（不足一分钟时为小数）。

10 秒是轮询间隔下限，不保证 10 秒内送达；分页、下载、上传和平台延迟会延长实际时间。一轮未结束不会并发启动下一轮。每轮仍读取完整收藏列表，高频轮询会增加请求量，平台没有承诺该频率安全；建议先用默认 15 分钟，10 秒仅由用户主动选择。访问失败的退避基数至少 5 分钟（首次至少等 10 分钟），缩短间隔、重启和换模式均不会缩短已记录的失败退避。

日常设置存入该订阅的 SQLite，优先于配置文件里的 `sync_mode` / `poll_seconds`。运行中的 worker 每 2 秒检查本地控制状态；当前一条完成后应用模式变化，不中断已经发出的上传/消息。模式设置不启动后台进程，也不打开 `send_enabled`。

`sync` 不改变模式：只扫描一次，处理当次发现的新增与之前尚未发送的 `queued` 队列，之后新加入的收藏留待下次。首次只建基线；失败或送达不明的任务不会自动重试。重复发起会合并到当前任务。冷却时显示等待时间，发生风控仍暂停。

手动任务会按顺序处理本次全部待发视频，默认每次投递开始至少间隔 10 秒（`delivery_gap_seconds`，下载耗时也计入）。自动模式仍遵守每轮 `max_sends_per_cycle` 上限。`sync --cancel` 只取消本次任务、保留待发队列；若还要停止后续自动处理，同时切换手动模式。

已有持续 worker 时，`sync` 提交任务后返回，通过 `status` 查看结果；没有其他进程持锁时在当前终端执行到完成，可能等待冷却，Ctrl+C 取消本次任务。不要把命令返回的 `queued` 或 `waiting` 说成“发送成功”。若占锁的是短时 `scan` / `bind` 而非持续 worker，请在其结束后再次运行 `sync` 或启动 `run`。

本地向导完成后也提供同步、取消、模式/间隔及后台启停操作，与 CLI 共用控制服务。设置页只监听随机回环端口，并校验会话令牌与浏览器来源，不是公网管理后台。

## 失败与恢复

`status` 是本地读取，不请求 B 站或 QQ；worker 输出逐轮 JSON，可交给服务管理器留存日志。

| 状态 | 行为 |
| --- | --- |
| `queued` | 已识别新增，等待投递 |
| `preparing` | 下载、完整解码与上传；此阶段不发消息 |
| `sent` | QQ 返回了消息 ID；实际播放效果仍需群内验证 |
| `failed` | 下载/上传失败，或已知明确的发送权限拒绝；任务保留并暂停投递 |
| `uncertain` | 消息请求超时、缺少回执或发送阶段崩溃；停止自动重试，核对群后处理 |

```sh
# 解决下载/上传或明确的发送权限问题后，将指定 failed 任务重新入队
bili-fav-relay retry BVxxxxxxxxxx

# 先人工核对群，再告诉程序 uncertain 任务是否已经送达
bili-fav-relay resolve BVxxxxxxxxxx --delivered yes
# 确认没有送达，才允许再次入队
bili-fav-relay resolve BVxxxxxxxxxx --delivered no

# 解除暂停；原有冷却时间仍生效
bili-fav-relay resume
```

QQ 错误 `40034105` 表示主动消息被拒。设置页与 `status` 的 `control.delivery_problem` 会保留数字码、HTTP 状态和开启群内“主动发言”权限的指引，任务记为 `failed`，不会自动重发。开启权限并确认无其他暂停原因后：先 `stop`，通过 `status` 确认后台已停止，再依次 `retry <BV号>`、`resume`、`sync`；需要持续运行时再 `start`。带 `--config` 的用法见上文。

超时、缺少回执、未知拒绝或 5xx 仍按 `uncertain` 处理。升级前已存在的 `uncertain` 不会自动改成 `failed`，仍须核对群后使用 `resolve`。

B 站风控、权限拒绝或分页超预算会暂停源访问；普通网络失败按不低于 5 分钟的基数指数退避，上限一天。不会轮换代理、读浏览器 Cookie 或处理验证码。移除后重新加入、重启程序都不会再次发送已经见过的 BV。

## 默认边界

- 公开收藏夹、普通投稿；私密、付费、登录限制、直播和番剧不支持。
- 请求 `qn=16`，实际规格以下载校验报告为准。
- 默认拒绝多 P 投稿，避免只发第一 P 却声称整部发送成功。若你只想发 P1，显式设置 `multipart_policy: "first"`。
- 默认视频时长上限 30 分钟、MP4 上限 30,000,000 字节，以保持 QQ 视频消息而非降级文件。不自动压缩长视频；超额任务明确失败。
- 自动扫描间隔 10 秒至 24 小时，默认 15 分钟；手动同步也不突破 10 秒源请求冷却。
- 默认本地缓存预算 1GB，超额暂停下载；已下载视频和失败片段保留在 `data/media/`。清理前先核对队列，删除已发送视频目录即可释放空间；保留数据库就保留去重记录。
- 每次轮询读取完整列表（默认上限 100 条），不会凭总数相同判断没有新增。两次轮询之间加入又移除的内容可能无法发现。
- 暂不提供自动更新、远程控制、多租户托管、MCP 或插件市场包装。
- “从这条指令的时间开始，只转发之后收藏的视频”的临时会话尚未实现；当前 sync 包含之前待发队列，不能冒充该功能。

## 备份与卸载

```sh
bili-fav-relay backup --output /安全位置/relay-state.sqlite3
```

停止 worker 后，同时备份配置、`data/binding.json`、环境变量的安全来源。系统凭据库中的密钥不包含在配置备份里，换电脑后需重新在向导填写。恢复时将备份数据库放回 `data/state.sqlite3`；不要把旧 `-wal` / `-shm` 文件混入恢复目录。备份恢复去重行为已用自动测试验证。升级前先备份；0.1 使用 schema v1，不兼容的新版本应先迁移，禁止直接改生产表结构。

卸载：先 `stop` 并通过 `status` 确认停止，关闭设置服务，再删除虚拟环境和项目目录；在系统凭据管理器里删除 `bili-fav-relay` 对应条目（配置的 credential_id 是其引用）。不会向外部遥测服务发送信息；本地保存收藏夹 BV、标题、队列状态、视频和 QQ 群绑定，凭据仅从环境或系统凭据库读取。仅转发你有权分享的内容。

## 开发与发布

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

验证状态见 [docs/verification.md](docs/verification.md)，架构见 [docs/architecture.md](docs/architecture.md)，设计取舍见 [docs/adr.md](docs/adr.md)。程序代码及文档文字采用 [MIT 许可证](LICENSE)；哈基米、大肥鱼人物素材及包含它们的衍生图片单独采用 CC BY-NC-SA 4.0，不属于 MIT 范围。署名、来源和文件清单见 [ARTWORK_LICENSE.md](ARTWORK_LICENSE.md)，其他组件见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。分发安装包时保留两类许可及来源声明，不得将完整包描述为全部适用 MIT，也不得把代码许可解释为人物素材的商业使用许可。

当前是开发版；长期运行、干净机器与跨平台安装验收仍待完成，详见验证文档。如果你 fork 后提交改动，不要把 `data/`、`.env`、`config.json` 等本地运行文件提交上去（`.gitignore` 已排除）。
