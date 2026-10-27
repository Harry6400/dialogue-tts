# 对白 · XML → MP3 · Windows / Mac

在紧凑桌面界面导入双声道 XML，固定角色音色，逐通导出 MP3。两个桌面版本使用同一份自有服务与界面源码。

## 给使用者

打开构建后的 Windows 应用或 Mac `.app` 即可。应用自动打开电脑的默认浏览器，后台只监听本机临时端口；不需要另配服务器、安装 Python、下载模型或安装 FFmpeg。

**音频由微软在线服务生成，仍然需要联网。** 只有确认发送后才将脱敏文本交给微软；预览不触达微软。原始 XML 不写回、不改写，不克隆原录音中的真实声音。

下载音频后，点击右上角“退出应用”结束后台并清理临时文件。仅关闭网页标签不会退出后台。

“检查更新”只读取 https://github.com/Harry6400/dialogue-tts 的正式 Release。发现新版后，“获取更新”打开本仓库官方发布页；下载对应系统版本、退出旧程序，再替换应用。不会后台执行下载内容，不会伪称已静默升级。

## 体积与依赖

- 不包含语音模型、FFmpeg、pydub、numpy 或内置 Chromium。
- 使用默认浏览器；Python 服务与必要库由构建器内置。
- Edge 固定格式 MP3 帧直接拼接，并插入自有静音样本。当前格式为 24kHz / 48kbps / mono MPEG2 Layer III。
- 停顿按 24ms 帧取整，和设置值最多相差 12ms；网络服务格式变更时明确报错，不猜测拼接。

## 构建者

Python 3.9+。Mac 入口 `mac_launcher.py`；Windows 构建由对应入口/工作流指定，两者共享 `web_dialogue/`。

```sh
python -m pip install -r web_dialogue/requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onedir --windowed --name 'DialogueTTS' \
  --add-data 'web_dialogue/index.html:web_dialogue' \
  --add-data 'web_dialogue/silence.mp3:web_dialogue' \
  --add-data 'third_party:third_party' \
  --hidden-import web_dialogue.worker --hidden-import web_dialogue.mp3_frames \
  --collect-all edge_tts --collect-all certifi mac_launcher.py
```

请按实际平台调整资源分隔符与入口，不把示例命令当作已经执行的跨平台验证。冻结子进程通过 `--worker` 模式工作，无需系统 Python。保留系统可信 CA，再添加随包 certifi 根证书；不得跳过 TLS 验证。

资源必须包括 `index.html`、`silence.mp3` 和全部许可。可按实际 PyInstaller 版本配置运行时钩子、签名和架构。构建者要验证冻结程序的资源路径、原生依赖、架构、签名及退出行为。

## 格式

只接受 analyze / subject / function 结构的双声道 XML；text 为文本、time 为起止时间，callInfo/role 必须为 AGENT 或 USER。默认 AGENT 用晓晓、USER 用晓伊，均为普通话女声，可分别更改。没有字段映射或粘贴 JSON 入口。

一次最多100段、总20000字符、每段5000字符。语速0.5–2倍，停顿0–3000ms。新音频与新时间轴不等于原录音时长/时间戳。

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
同时选择同一通对话的两份 XML。支持 analyze/subject/function 的 text 与 time，以及 callInfo/role 声道标记。按开始时间全局稳定排序，不会先读完一个文件再读另一个。此结构没有显式时间单位，默认毫秒并在预览中提示，可改成秒。

mutedSegments 时间区间重叠的段落不显示原文、不发送合成，预览为“已静音片段”并计数；不会恢复遮蔽文本。callInfo 电话、内部标识和其他分析字段不进入导出或TTS。合成按顺序串行，不能还原原录音的重叠说话或时长。

DTD、实体、外部引用被拒绝；使用 defusedxml 0.7.1（PSF许可，见third_party）。examples中的两XML是完全虚构示例，不是真实录音转写。

## Mac 原生窗口构建

Mac 最终入口为 native_macos/App.swift，使用系统 AppKit / WKWebView 打开应用自己的窗口；mac_launcher.py 仅作为内置 Python 服务，不打开默认浏览器。后台只监听 127.0.0.1 随机端口，关窗时退出服务并清理任务。XML 选择、MP3 保存使用系统文件面板；应用内拒绝外域导航，官方 GitHub 更新页由系统浏览器打开。

Apple Silicon、macOS 12 或更新版本，安装 Swift Command Line Tools。安装 web_dialogue/requirements.txt 和 PyInstaller 后运行 python tools/build_macos.py。脚本只构建并静态验证，不启动最终应用。分发包包含实际依赖许可证与可重建源码，无 FFmpeg、语音模型或 Chromium。
