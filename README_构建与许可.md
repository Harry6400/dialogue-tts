# 对白 · XML → MP3 · Windows / Mac

在紧凑桌面界面导入双声道 XML，固定角色音色，逐通导出 MP3。两个桌面版本使用同一份自有服务与界面源码。

## 给使用者

打开构建后的 Windows 应用或 Mac `.app` 即可。应用自动打开电脑的默认浏览器，后台只监听本机临时端口；不需要另配服务器、安装 Python、下载模型或安装 FFmpeg。

**音频由微软在线服务生成，仍然需要联网。** 只有确认发送后才将本次文本交给微软；用户需自行确认脱敏；预览不触达微软。原始 XML 不写回、不改写，不克隆原录音中的真实声音。

原生工作台支持大目录扫描、自动分批与重启恢复。退出后保留未结束任务；重新打开核验目录与结果，经确认继续。普通旧浏览器模式的临时任务仍需先保存。

“检查更新”优先使用 Gitee 国内分发源，网络故障时回退 GitHub。同一更新只接受选定版本、文件名、大小和 SHA-256 一致的附件；摘要、身份和 TLS 校验失败不会靠换源掩盖。下载和校验通过后，经确认正常退出、安装、重启并检查启动回执。系统安全确认仍需正常处理；启动状态未知时保留新旧副本并提示处理。当前分支尚未发布。Mac 已安装 v0.3.x 需首次手动安装未来的修复版本，之后再自动更新；旧 ZIP 和更新器问题详见 docs/PROJECT_STATUS.md。

## 体积与依赖

- 不包含语音模型、FFmpeg、pydub、numpy 或内置 Chromium。
- Mac 使用系统 WKWebView，Windows 使用系统 WebView2；Python 服务与必要库由构建器内置。
- Edge 固定格式 MP3 帧直接拼接，并插入自有静音样本。当前格式为 24kHz / 48kbps / mono MPEG2 Layer III。
- 停顿按 24ms 帧取整，和设置值最多相差 12ms；网络服务格式变更时明确报错，不猜测拼接。

## 构建者

Python 3.12，Node 22.x。使用项目虚拟环境和 npm workspace；Node 仅用于开发构建；原生模式加载 `frontend/dist` 的生产工作台。无原生授权的旧 HTTP 模式保留 `web_dialogue/index.html`。开发入口、规则和状态分别见 AGENTS.md、PRODUCT.md、docs/PROJECT_STATUS.md。

```sh
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python requirements-dev.lock
npm ci
.venv/bin/python tools/check.py
```

Mac 构建需要 Swift 命令行工具：

```sh
uv pip sync --python .venv/bin/python requirements-macos-build.lock
.venv/bin/python tools/build_macos.py
```

Windows 使用 `.venv\Scripts\python.exe`，依赖锁为 `requirements-windows-build.lock`，构建入口为 `tools/build_windows.py`。GitHub CI 同时运行 Mac/Windows 的离线检查及单独的 Windows 冻结构建和原生检查。

React 工作台开发服务器为 `npm run dev:frontend`，代理指向本机 8877；完整目录功能需要由原生壳启用的工作区服务，不使用浏览器传路径授权。双平台构建脚本会先构建生产前端；也可单独运行 `npm run build --workspace frontend`。Host、Origin、页面令牌与原生私有令牌分别校验。运行与恢复契约见 `docs/workspaces.md`。

请按实际平台调整资源分隔符与入口，不把示例命令当作已经执行的跨平台验证。冻结子进程通过 `--worker` 模式工作，无需系统 Python。保留系统可信 CA，再添加随包 certifi 根证书；不得跳过 TLS 验证。

资源必须包括旧 `index.html`、`frontend/dist`、`silence.mp3` 和全部 Python／前端运行时许可。可按实际 PyInstaller 版本配置运行时钩子、签名和架构。构建者要验证冻结程序的资源路径、原生依赖、架构、签名及退出行为。

## 格式

只接受 analyze / subject / function 结构的双声道 XML；text 为文本、time 为起止时间，callInfo/role 必须为 AGENT 或 USER。内置六种大陆普通话音色，启动不访问在线目录。客服默认晓晓、客户默认晓伊；默认根据明确称呼匹配客户声音，不明确或冲突时保留整体设置，逐通手动选择优先。旧外语音色需用户重新选择后才写回。没有字段映射或粘贴 JSON 入口。

每通最多100段、总20000字符、每段5000字符；单份 XML 最多 2 MiB。目录文件总数不受旧 100 份导入限制。语速0.5–2倍，停顿0–3000ms。新音频与新时间轴不等于原录音时长/时间戳。

## 源码与许可

本版完全独立，无 MH.Tools.TtsAndSrtGenerate 源码或 imports。

- 自有应用代码：MIT，见根 `LICENSE`。
- edge-tts 7.3.1：主体 LGPLv3，只有 srt_composer.py 是 MIT；见 `third_party/edge-tts-LICENSE.txt`、`third_party/GPL-3.0.txt` 与 `NOTICE`。
- 当前不再包含 FFmpeg 或 pydub。
- 完整自有源码与构建说明随版本提供，使用者可以修改/替换 LGPL 组件并重建。不得剥夺 LGPL 许可中的相关修改与调试权利。
- 二进制构建需保留实际依赖的许可证、版权与对应源码/重建资料，而不是把所有依赖笼统称为 MIT。

代码许可不等于微软在线服务授权，不承诺永久免费、无限或生产级稳定。

## 开发验证

```sh
python -m unittest discover -s web_dialogue -t . -p 'test_*.py' -v
```

源码 HTTP 合成、帧拼接、解码、输入/错误/取消/超时/更新逻辑均有测试。真实浏览器渲染及最终 Windows/Mac 程序是否已经验收，须以对应构建结果说明为准，不把源码测试等同成品启动成功。

## 双 XML 声道导入
同时选择同一通对话的两份 XML。支持 analyze/subject/function 的 text 与 time，以及 callInfo/role 声道标记。按开始时间全局稳定排序，不会先读完一个文件再读另一个。时间固定毫秒，同起点时客服在前，同角色保留原段落顺序；显式传入秒单位会报错。文件角色来自 XML，不需要交换 A/B。

mutedSegments 时间区间重叠的段落不显示原文、不发送合成，预览为“已静音片段”并计数；不会恢复遮蔽文本。callInfo 电话、内部标识和其他分析字段不进入导出或TTS。合成按顺序串行，不能还原原录音的重叠说话或时长。

DTD、实体、外部引用被拒绝；使用 defusedxml 0.7.1（PSF许可，见third_party）。examples中的两XML是完全虚构示例，不是真实录音转写。

## Mac 原生窗口构建

Mac 最终入口为 native_macos/App.swift，使用系统 AppKit / WKWebView 打开应用自己的窗口；mac_launcher.py 仅作为内置 Python 服务，不打开默认浏览器。后台只监听 127.0.0.1 随机端口，关窗时退出服务并保留未结束工作区的恢复状态。XML 选择、MP3 保存使用系统文件面板；应用内拒绝外域导航，官方 GitHub 更新页由系统浏览器打开。

Apple Silicon、macOS 12 或更新版本，安装 Swift Command Line Tools。安装 web_dialogue/requirements.txt 和 PyInstaller 后运行 python tools/build_macos.py。脚本只构建并静态验证，不启动最终应用。分发包包含实际依赖许可证与可重建源码，无 FFmpeg、语音模型或 Chromium。

## 保存与退出

批量生成前必须通过原生面板选择有效结果文件夹，并完成可写性和空间检查。每通完成后自动保存，真实回执才显示已保存。保存失败保留临时音频，可重试保存而不重新合成；单通可生成后手动保存。更改参数或解析失败时保留上一份结果入口，并标明它对应旧设置。

关窗、退出按钮和退出菜单共用任务与保存保护。保存中或状态未知时保持打开；未保存结果可返回保存、明确放弃或取消退出。原生工作区的完整未保存结果与计划会保留以供恢复；明确结束或放弃后清理任务索引与缓存，保留实际 MP3 和可信完成账本。旧临时任务仍沿用原退出保护。
