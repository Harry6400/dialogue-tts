# 架构与迁移边界

## 当前运行结构

Mac 的 AppKit/WKWebView 和 Windows 的 pywebview/WebView2 加载同一份本机页面。Python HTTP 服务仅监听本机，处理配置、XML 预览、任务、音频和更新互斥。原生壳持有目录授权、保存面板、保存回执及系统打开权限。

解析入口经 normalize 与 xml_pair 验证 XML。原生工作台经 Workspaces 流式扫描、全局配对，SQLite 保存索引与可恢复计划；Jobs 管理当前独立 worker，原生授权适配器负责受限目录读写与可信回执。旧 batch_import/Batches 接口保留兼容回归。worker 调用在线语音服务，再由 mp3_frames 验证固定格式并串接音频。fingerprints 根据实际文本、声音和设置识别完成结果。

文件路径不作为前端可编辑权限。原生层发出目录能力，校验实际输出大小和摘要后才确认保存；生成和保存状态分开。新目录桥只把能力与状态交给页面，正文由可信本机适配器按需受限读取；旧批量入口的 100 文件、50 通边界不适用于新工作区索引。

## 工作区、缓存与恢复

`Workspaces` 通过 SQLite 保存全目录元数据、选择快照和冻结执行计划，不保存 XML 正文。流式扫描完成全局配对与重名检查后，才允许后台串行派发。页面按服务端筛选和分页读取摘要，单通正文另行查询；页面刷新和切换不持有执行队列。

每通音频先原子发布到持久缓存，再由 `NativeWorkspaceAccess` 写入已授权结果目录。该适配器在独立 SQLite 账本中保存发布意图与实际完成回执，拒绝覆盖同名输出。`Workspaces` 在启动、摘要、安全检查及清理前调和实际缓存和数据库状态，覆盖“缓存已发布、状态未提交”的窗口；有效完整缓存恢复为待保存，部分 `.tmp` 不冒充完整音频，未知缓存按保留和阻挡处理。

重启不会自动合成或自动激活目录授权。扫描、设置重算和输出核验分别保留 `recovery_scan`、`recovery_settings`、`recovery_checking` 阶段；各后台操作支持取消。未完成的设置重算禁止生成，输出核验保留已完成部分。声音重算先检查来源目录授权，生成前再比较输入摘要。已有完整音频优先保存；缺少完整音频的中断通话需要重新生成，不提供段落级续传。

全局 `Workspaces.safety()` 汇总所有工作区，并依据实际缓存报告未保存结果。页面通过 `/api/workspaces/safety` 读取，Mac 原生通过独立令牌的 `/api/native/workspaces/safety` 读取，Windows 原生直接调用服务对象。四个安全字段均要求布尔类型；异常、缺失或类型不符时阻挡退出和更新。当前可见页面的空闲状态不能替代全局检查。

## 更新

update_sources 选择 Gitee 优先的版本；启动下载或替换流程前，检查全部工作区以及页面和原生保存状态。安全下载、解包和身份验证后才启动独立 helper。helper 等待旧实例正常退出、保留旧副本、启动候选并核对原生就绪回执。系统确认、状态未知和校验失败不能当作成功。

新工作区接口及恢复阶段见 [workspaces.md](workspaces.md)；旧目录桥和更新桥分别见 [folder-bridge.md](folder-bridge.md)、[updater-bridge.md](updater-bridge.md)。历史文档与当前实现不一致时，必须以审阅和实际验证修正，不按旧结论操作。

## React 工作台

frontend 是 React 开发和构建工程，原生 workspace 模式加载其生产构建。services 层集中处理 HTTP、Mac 和 Windows 桥；types 描述现有契约，测试适配器只用于隔离验证。

Python 只提供构建入口与受限 JS/CSS 资源路径；缺少构建资源时明确失败。双平台打包前运行锁定前端构建，并包含实际 React 运行时许可。Vite 仅用于开发，分发包不启动 Vite，最终用户无需安装 Node 或 Python。普通无原生目录授权的旧 HTTP 模式保留旧页面。新工作台与持久存储契约见 workspaces.md。
