import Cocoa
import WebKit
import UniformTypeIdentifiers
import Darwin

final class SavedDownloadRegistry {
    private var pending: [ObjectIdentifier: URL] = [:]
    private var saved: [String: URL] = [:]

    private func isSavedFile(_ url: URL) -> Bool {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path) else { return false }
        return attributes[.type] as? FileAttributeType == .typeRegular
    }

    func selected(_ url: URL?, for download: ObjectIdentifier) {
        pending.removeValue(forKey: download)
        if let url = url, url.isFileURL { pending[download] = url.standardizedFileURL }
    }

    func failed(_ download: ObjectIdentifier) { pending.removeValue(forKey: download) }

    func completed(_ download: ObjectIdentifier) -> [String: String]? {
        guard let url = pending.removeValue(forKey: download), isSavedFile(url) else { return nil }
        let id = UUID().uuidString
        saved[id] = url
        return ["save_id": id, "name": url.lastPathComponent, "path": url.path]
    }

    func target(for body: Any) -> URL? {
        guard let request = body as? [String: Any], Set(request.keys) == ["save_id"],
              let id = request["save_id"] as? String, let url = saved[id], isSavedFile(url) else { return nil }
        return url
    }

    func clear() { pending.removeAll(); saved.removeAll() }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var service: Process?
    private var baseURL: URL?
    private var closing = false
    private let savedDownloads = SavedDownloadRegistry()
    private var trustedDownloads = Set<ObjectIdentifier>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let item = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "退出对白", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        menu.addItem(item)
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "编辑")
        for (title, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        editItem.submenu = editMenu
        menu.addItem(editItem)
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "对白"
        window.minSize = NSSize(width: 880, height: 650)
        window.delegate = self
        window.isReleasedWhenClosed = false
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, name: "dialogueReveal")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startService()
    }

    private func startService() {
        let process = Process()
        let pipe = Pipe()
        process.executableURL = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/DialogueTTSService")
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        service = process
        var buffer = Data()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            buffer.append(data)
            if let newline = buffer.firstIndex(of: 10) {
                handle.readabilityHandler = nil
                let line = String(data: buffer[..<newline], encoding: .utf8) ?? ""
                DispatchQueue.main.async {
                    guard let self = self, !self.closing,
                          let url = URL(string: line), url.scheme == "http", url.host == "127.0.0.1",
                          let port = url.port, port > 0, port < 65536, url.user == nil, url.password == nil else { return }
                    self.baseURL = url
                    self.webView.load(URLRequest(url: url))
                }
            } else if buffer.count > 1024 { handle.readabilityHandler = nil }
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, !self.closing else { return }
                if self.baseURL == nil { self.showStartupError() }
                NSApp.terminate(nil)
            }
        }
        do { try process.run() } catch { showStartupError(); NSApp.terminate(nil); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
            guard let self = self, self.baseURL == nil, !self.closing else { return }
            self.showStartupError()
            NSApp.terminate(nil)
        }
    }

    private func showStartupError() {
        let alert = NSAlert()
        alert.messageText = "对白未能启动"
        alert.informativeText = "请确认应用文件完整后重试。原文件没有被修改。"
        alert.alertStyle = .critical
        alert.runModal()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func windowWillClose(_ notification: Notification) { clearSaveState() }

    private func clearSaveState() {
        savedDownloads.clear()
        trustedDownloads.removeAll()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "dialogueReveal")
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if closing { return .terminateLater }
        closing = true
        clearSaveState()
        guard let process = service, process.isRunning else { return .terminateNow }
        process.terminate()
        DispatchQueue.global(qos: .userInitiated).async {
            let deadline = Date().addingTimeInterval(6)
            while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    private func isLocal(_ url: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.port == baseURL?.port && url.user == nil && url.password == nil
    }

    private func isWorkbench(_ url: URL?) -> Bool {
        guard let url = url else { return false }
        return isLocal(url) && (url.path.isEmpty || url.path == "/") && url.query == nil
    }

    private func notifyPage(_ function: String, payload: [String: Any]) {
        guard !closing, isWorkbench(webView.url),
              let bytes = try? JSONSerialization.data(withJSONObject: payload),
              let json = String(data: bytes, encoding: .utf8) else { return }
        webView.evaluateJavaScript("if (typeof window.\(function) === 'function') window.\(function)(\(json));", completionHandler: nil)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard !closing, message.name == "dialogueReveal", message.webView === webView,
              message.frameInfo.isMainFrame, origin.`protocol` == "http", origin.host == "127.0.0.1",
              origin.port == baseURL?.port, isWorkbench(message.frameInfo.request.url), isWorkbench(webView.url) else { return }
        guard let url = savedDownloads.target(for: message.body) else {
            notifyPage("dialogueNativeRevealResult", payload: ["ok": false])
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([url])
        notifyPage("dialogueNativeRevealResult", payload: ["ok": true])
    }

    private func isRelease(_ url: URL) -> Bool {
        let path = url.path.removingPercentEncoding ?? url.path
        return url.scheme == "https" && url.host == "github.com" && url.port == nil && url.user == nil && url.password == nil
            && url.query == nil && url.fragment == nil && path.hasPrefix("/Harry6400/dialogue-tts/releases/")
            && !path.split(separator: "/").contains("..") && !path.contains("\\")
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if isLocal(url) { decisionHandler(action.shouldPerformDownload ? .download : .allow); return }
        if isRelease(url) && action.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = response.response.url, isLocal(url) else { decisionHandler(.cancel); return }
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, isRelease(url), action.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.allowedContentTypes = [.xml]
        panel.beginSheetModal(for: window) { response in completionHandler(response == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "确认")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    private func prepareDownload(_ download: WKDownload, source: URL?) {
        guard !closing, let source = source, isLocal(source), isWorkbench(webView.url) else {
            download.cancel { _ in }
            return
        }
        trustedDownloads.insert(ObjectIdentifier(download))
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        prepareDownload(download, source: navigationAction.request.url)
    }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        prepareDownload(download, source: navigationResponse.response.url)
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let id = ObjectIdentifier(download)
        guard !closing, trustedDownloads.contains(id), let source = response.url, isLocal(source) else {
            completionHandler(nil)
            return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] result in
            guard let self = self, !self.closing else { completionHandler(nil); return }
            let url = result == .OK ? panel.url : nil
            self.savedDownloads.selected(url, for: id)
            if url == nil { self.trustedDownloads.remove(id) }
            completionHandler(url)
        }
    }

    func downloadDidFinish(_ download: WKDownload) {
        let id = ObjectIdentifier(download)
        guard trustedDownloads.remove(id) != nil else { return }
        if let record = savedDownloads.completed(id) {
            notifyPage("dialogueNativeSaved", payload: record)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let id = ObjectIdentifier(download)
        trustedDownloads.remove(id)
        savedDownloads.failed(id)
        guard !closing else { return }
        let detail = error as NSError
        if detail.code == NSURLErrorCancelled || detail.code == NSUserCancelledError { return }
        let alert = NSAlert()
        alert.messageText = "下载没有完成"
        alert.informativeText = "请保留应用窗口，稍后重试。"
        alert.beginSheetModal(for: window)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
