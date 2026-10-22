# Windows 部署与设置页错误提示验证

环境：Windows、Python 3.14.5 虚拟环境、websockets 16.1.1、keyring 25.6.0。系统凭据后端识别为 WinVaultKeyring；FFmpeg/ffprobe 9.0.2 通过合成 H.264 MP4 编码与探测，支持 `-enc_time_base:v demux`。

发现并修复：

1. 持锁时读取整个锁文件包含被 Windows 锁定的首字节，导致 status 报 PermissionError。读取改为先 seek(1)，原有持锁状态与控制测试通过。
2. Windows 虚拟环境启动器 PID 与实际 worker PID 不同，原握手超时但后台已经运行。改为启动端生成随机会话标识，经子进程环境传给 worker；后台启动、防重复启动和协作停止测试通过。
3. 新浏览器会话只打开本地基础地址时，状态接口正确拒绝缺少凭证的请求，但宽屏 CSS 隐藏通知，角色又优先显示等待，页面持续显示“正在读取设置”。首次失败改为显示错误和恢复指引；真实浏览器验证缺少凭证时显示“无法读取设置”，使用完整链接后正常进入“想分享哪个收藏夹？”。
4. 原 HTTP 设置测试默认使用真实收藏夹适配器，已改用 FakeSource；Windows 进程测试通过协作停止退出实际 worker，等待启动器退出后再清理临时文件。

验证命令：

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
node tests/test_ui_lines.mjs
```

83 项 Python 测试及 JavaScript 台词回归通过。上述修复后的完整测试使用离线平台替身，浏览器验证只访问本机设置服务。未在本轮测试真实 QQ 群投递，也未在 Linux/macOS 实机复测这些修改。Node.js 仅用于可选的开发测试，普通安装与运行仍不依赖 Node.js。
