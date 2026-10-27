# 对白 · XML → MP3 · Windows / Mac

在桌面界面中导入双声道 XML，固定角色音色，逐通导出 MP3。两个桌面版本使用同一份自有服务与界面源码。

## 给使用者

打开构建后的 Windows 应用或 Mac `.app` 即可。Mac 版使用系统 WKWebView 打开应用自己的窗口，后台只监听本机临时端口；不需要另配服务器、安装 Python、下载模型或安装 FFmpeg。

**音频由微软在线服务生成，仍然需要联网。** 只有确认发送后才将脱敏文本交给微软；预览不触达微软。原始 XML 不写回、不改写，不克隆原录音中的真实声音。

下载音频后，点击右上角“退出应用”结束后台并清理临时文件。Mac 原生窗口关闭时也会退出后台。

“检查更新”只读取 https://github.com/Harry6400/dialogue-tts 的正式 Release。发现新版后，“获取更新”打开本仓库官方发布页；下载对应系统版本、退出旧程序，再替换应用。不会后台执行下载内容，不会伪称已静默升级。

## 体积与依赖

- 不包含语音模型、FFmpeg、pydub、numpy 或内置 Chromium。
- Mac 使用系统 WebKit，无需捆绑浏览器运行时；Python 服务与必要库由构建器内置。
- Edge 固定格式 MP3 帧直接拼接，并插入自有静音样本。当前格式为 24kHz / 48kbps / mono MPEG2 Layer III。
- 停顿按 24ms 帧取整，和设置值最多相差 12ms；网络服务格式变更时明确报错，不猜测拼接。

## 构建者

Python 3.9+。Mac 原生入口 `native_macos/App.swift`，内置服务入口 `mac_launcher.py`；Windows 构建由对应入口/工作流指定，两者共享 `web_dialogue/`。

```sh
python -m pip install -r web_dialogue/requirements.txt pyinstaller
python tools/build_macos.py
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

## 批量处理（0.2）

多份 XML 按非空通话标识与开始时间配对，缺轨、重复、冲突或无法确定的组不会猜配，可明确手工配对或排除。仅支持上述固定 XML 结构。

每批最多 40 份文件、20 通对话，页面合计 4MB，每份 2MB；每通仍受上文段数与字数限制。逐通顺序生成，失败不阻断后续；“重试失败”不重新合成成功或已取消项。每通独立 MP3，ZIP 仅含成功项，最多 200MB，使用自动编号避免暴露通话标识。所有临时批次在退出时删除，最多保留 5 批，请及时下载。

### 文件夹批处理（v0.3）

选择来源文件夹后会自动预览本层 XML；补齐缺失声道后点“刷新”。按 XML 内完全一致的 `callInfo/callReference` 配对，每通必须有一份 AGENT 和一份 USER；不按文件名或相邻顺序猜测。最多 100 个文件、50 通，每份 2 MiB、合计 20 MiB。

选择“结果文件夹”后，每通成功音频会保存到那里；同名文件自动编号、不覆盖。保存失败可以单独“重试保存”，不必重新合成。程序会记住文件夹及音色，但下次打开只恢复显示，需点击刷新才读取文件。

已经按相同文本和声音设置生成、且文件校验仍有效的通话默认跳过。需要再做一次时勾选“重新生成已完成的通话”。“生成完成”和“已保存”是两个独立状态；以界面显示的实际保存路径为准。
