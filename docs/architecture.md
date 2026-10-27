# 架构与迁移边界

## 当前运行结构

Mac 的 AppKit/WKWebView 和 Windows 的 pywebview/WebView2 加载同一份本机页面。Python HTTP 服务仅监听本机，处理配置、XML 预览、任务、音频和更新互斥。原生壳持有目录授权、保存面板、保存回执及系统打开权限。

解析入口经 normalize 与 xml_pair 验证 XML；batch_import 按通话标识分组。Jobs 管理独立 worker，Batches 顺序调度并保留逐通结果。worker 调用在线语音服务，再由 mp3_frames 验证固定格式并串接音频。fingerprints 根据实际文本、声音和设置识别完成结果。

文件路径不作为前端可编辑权限。原生层发出目录能力，校验实际输出大小和摘要后才确认保存；生成和保存状态分开。

## 更新

update_sources 选择 Gitee 优先的版本；安全下载、解包和身份验证后才启动独立 helper。helper 等待旧实例正常退出、保留旧副本、启动候选并核对原生就绪回执。系统确认、状态未知和校验失败不能当作成功。

接口契约见 folder-bridge.md、updater-bridge.md。历史文档与当前实现不一致时，必须以审阅和实际验证修正，不按旧结论操作。

## React 准备工程

frontend 是独立开发和构建工程，本轮不是正式应用入口。services 层集中处理 HTTP、Mac 和 Windows 桥；types 描述已有契约，测试适配器只用于隔离验证。

未来切换正式入口必须同时接入 Python 静态资源白名单、前端构建物、双平台打包和真实 WebView 验证；不把 Vite 开发服务带入分发包。最终用户仍不需要 Node 或 Python。
