import Cocoa
import WebKit
import UniformTypeIdentifiers
import Darwin
import CryptoKit
import CoreFoundation

let updateMenuCheckScript = """
(() => {
    if (typeof window.dialogueCheckForUpdates === 'function') {
        window.dialogueCheckForUpdates();
        return 'react';
    }
    const button = document.getElementById('checkupdate');
    if (button && typeof button.click === 'function') {
        if (button.disabled) return 'busy';
        button.click();
        return 'legacy';
    }
    return 'unavailable';
})();
"""

func trustedNativeAudioMetadata(_ response: URLResponse) -> [String: Any]? {
    guard let response = response as? HTTPURLResponse, response.statusCode == 200, response.mimeType == "audio/mpeg",
          let url = response.url, url.query == nil, url.fragment == nil,
          url.path.range(of: "^/api/(jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/audio/[0-9]{3})$", options: .regularExpression) != nil,
          response.expectedContentLength > 0, response.expectedContentLength <= 256 * 1024 * 1024,
          let fingerprint = response.value(forHTTPHeaderField: "X-Dialogue-Fingerprint"), fingerprint.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
          let disposition = response.value(forHTTPHeaderField: "Content-Disposition"),
          let name = disposition.split(separator: "\"").dropFirst().first.map(String.init), FolderState.validName(name),
          disposition == "attachment; filename=\"\(name)\"" || disposition == "inline; filename=\"\(name)\"" else { return nil }
    return ["resource_path": url.path, "filename": name, "fingerprint": fingerprint, "size": response.expectedContentLength]
}

// terminateLater runs a nested AppKit loop. Starting it inside a main-queue
// block prevents the later main-queue reply from running until that block exits.
func scheduleNativeTermination(_ action: @escaping () -> Void) {
    RunLoop.main.perform(inModes: [.default], block: action)
}

func mergeNativeCloseSafety(_ page: [String: Any]?, _ backend: [String: Any]?) -> [String: Any]? {
    func boolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }
    guard let page = page, let backend = backend,
          let running = boolean(page["running"]), let saving = boolean(page["saving"]),
          let pending = boolean(page["pending"]), let unsaved = boolean(page["unsaved"]),
          let backendBusy = boolean(backend["busy"]), let backendSaving = boolean(backend["saving"]),
          let backendPending = boolean(backend["pending"]), let backendUnsaved = boolean(backend["unsaved"]) else { return nil }
    var result = page
    result["running"] = running || backendBusy
    result["saving"] = saving || backendSaving
    result["pending"] = pending || backendPending
    let legacyUnsaved: Bool
    if backend["legacy_unsaved"] != nil {
        guard let value = boolean(backend["legacy_unsaved"]) else { return nil }
        legacyUnsaved = value
    } else { legacyUnsaved = false }
    result["unsaved"] = unsaved || legacyUnsaved
    result["persistent_unsaved"] = backendUnsaved
    result["persistent_workspace"] = boolean(page["persistent_workspace"]) == true || backendBusy || backendUnsaved
    return result
}

// Only inspect the selected directory itself. Refuse oversized batches rather than truncating.
enum SelectedXMLFolderError: Error { case tooManyFiles, fileTooLarge, totalTooLarge, unreadable, unsupportedLinks, linkFailed, namesExhausted, nameConflict }

func readSelectedXMLFolder(_ directory: URL) throws -> [[String: String]] {
    let manager = FileManager.default
    let directoryFD = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
    guard directoryFD >= 0 else { throw SelectedXMLFolderError.unreadable }
    defer { close(directoryFD) }
    let entries = try manager.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey], options: [])
    var results: [[String: String]] = []
    var total = 0
    for url in entries.sorted(by: { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }) {
        guard url.pathExtension.lowercased() == "xml" else { continue }
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else { continue }
        guard results.count < 100 else { throw SelectedXMLFolderError.tooManyFiles }
        let descriptor = openat(directoryFD, url.lastPathComponent, O_RDONLY | O_NOFOLLOW)
        guard descriptor >= 0 else { throw SelectedXMLFolderError.unreadable }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        var statInfo = stat()
        guard fstat(descriptor, &statInfo) == 0, (statInfo.st_mode & S_IFMT) == S_IFREG else { throw SelectedXMLFolderError.unreadable }
        guard statInfo.st_size <= 2 * 1024 * 1024 else { throw SelectedXMLFolderError.fileTooLarge }
        let bytes = handle.readData(ofLength: 2 * 1024 * 1024 + 1)
        guard bytes.count <= 2 * 1024 * 1024 else { throw SelectedXMLFolderError.fileTooLarge }
        total += bytes.count
        guard total <= 20 * 1024 * 1024 else { throw SelectedXMLFolderError.totalTooLarge }
        guard let text = String(data: bytes, encoding: .utf8) else { throw SelectedXMLFolderError.unreadable }
        results.append(["name": url.lastPathComponent, "content": text])
    }
    return results
}

final class FolderState {
    private var urls: [String: URL] = [:]
    private var roles: [String: String] = [:]
    private var staleRoles = Set<String>()
    private var values: [String: Any] = [:]
    private let store: URL
    var preferencesURL: URL { store }
    private let persistHook: (() throws -> Void)?
    private let beforeLinkHook: (() throws -> Void)?
    private let afterLinkHook: (() throws -> Void)?
    private let allowedVoices = Set(["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-YunxiaNeural", "zh-CN-YunyangNeural"])

    init(storeOverride: URL? = nil, persistHook: (() throws -> Void)? = nil, beforeLinkHook: (() throws -> Void)? = nil, afterLinkHook: (() throws -> Void)? = nil) {
        self.persistHook = persistHook
        self.beforeLinkHook = beforeLinkHook
        self.afterLinkHook = afterLinkHook
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        store = storeOverride ?? support.appendingPathComponent("DialogueTTS", isDirectory: true).appendingPathComponent("preferences.json")
        if let data = try? Data(contentsOf: store), let decoded = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { values = decoded }
        for role in ["input", "output"] {
            guard let encoded = values[role] as? String, let data = Data(base64Encoded: encoded) else { continue }
            var stale = false
            guard let url = try? URL(resolvingBookmarkData: data, options: [.withoutUI], relativeTo: nil, bookmarkDataIsStale: &stale) else { continue }
            if stale { staleRoles.insert(role) }
            let id = UUID().uuidString
            urls[id] = url
            roles[role] = id
        }
    }
    private func persist() throws {
        try persistHook?()
        try FileManager.default.createDirectory(at: store.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let data = try JSONSerialization.data(withJSONObject: values)
        try data.write(to: store, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: store.path)
    }
    func compactMigratedReceipts() throws {
        let keys = ["records", "pending_outputs", "receipts", "exports"]
        guard keys.contains(where: { values[$0] != nil }) else { return }
        let previous = values
        keys.forEach { values.removeValue(forKey: $0) }
        do { try persist() } catch { values = previous; throw error }
    }
    func choose(_ url: URL, role: String) throws -> [String: Any] {
        let bookmark = try url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil)
        let old = values[role]
        values[role] = bookmark.base64EncodedString()
        do { try persist() } catch { values[role] = old; throw error }
        if let previous = roles[role] { urls.removeValue(forKey: previous) }
        let id = UUID().uuidString
        urls[id] = url
        roles[role] = id
        staleRoles.remove(role)
        return ["ok": true, "folder_id": id, "path": url.path]
    }
    func folder(_ id: String, role: String) -> URL? {
        guard roles[role] == id, !staleRoles.contains(role) else { return nil }
        return urls[id]
    }
    private func fileData(_ name: String, directory: URL) -> Data? {
        guard name == (name as NSString).lastPathComponent, name.hasSuffix(".mp3") else { return nil }
        let dir = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard dir >= 0 else { return nil }; defer { close(dir) }
        let fd = openat(dir, name, O_RDONLY | O_NOFOLLOW)
        guard fd >= 0 else { return nil }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, info.st_size > 0, info.st_size <= 256 * 1024 * 1024 else { return nil }
        let bytes = handle.readData(ofLength: Int(info.st_size) + 1)
        return bytes.count == info.st_size ? bytes : nil
    }
    private func verified(_ record: [String: Any], directory: URL) -> URL? {
        guard record["folder"] as? String == directory.path,
              let name = record["name"] as? String, let expectedSize = record["size"] as? Int,
              let expectedHash = record["sha256"] as? String, let data = fileData(name, directory: directory),
              data.count == expectedSize, Self.hash(data) == expectedHash else { return nil }
        return directory.appendingPathComponent(name)
    }
    private static func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    private var records: [[String: Any]] { values["records"] as? [[String: Any]] ?? [] }
    // A durable intent retains the hidden inode until its index entry is durable.
    // Recovery runs only when the user explicitly checks or saves in this folder.
    private func recoverPending(_ directory: URL) {
        let pending = values["pending_outputs"] as? [[String: Any]] ?? []
        guard !pending.isEmpty else { return }
        let dir = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard dir >= 0 else { return }; defer { close(dir) }
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: directory.path) else { return }
        var recovered: [[String: Any]] = []; var remaining: [[String: Any]] = []; var removable: [String] = []; var pruned = false
        for item in pending {
            guard item["folder"] as? String == directory.path, let temporary = item["temporary"] as? String,
                  temporary == (temporary as NSString).lastPathComponent,
                  temporary.range(of: "^\\.dialogue-[A-Fa-f0-9-]{36}\\.tmp$", options: .regularExpression) != nil else { remaining.append(item); continue }
            var original = stat()
            if fstatat(dir, temporary, &original, AT_SYMLINK_NOFOLLOW) != 0 {
                if errno == ENOENT { pruned = true } else { remaining.append(item) }
                continue
            }
            guard (original.st_mode & S_IFMT) == S_IFREG else { remaining.append(item); continue }
            var matched: [String: Any]?
            for name in names where name.hasSuffix(".mp3") {
                var candidate = stat()
                guard fstatat(dir, name, &candidate, AT_SYMLINK_NOFOLLOW) == 0, (candidate.st_mode & S_IFMT) == S_IFREG,
                      candidate.st_dev == original.st_dev, candidate.st_ino == original.st_ino else { continue }
                var record = item; record.removeValue(forKey: "temporary"); record.removeValue(forKey: "journal_id"); record["name"] = name
                if verified(record, directory: directory) != nil { matched = record; break }
            }
            if let record = matched { recovered.append(record); removable.append(temporary) }
            else { remaining.append(item) }
        }
        guard !recovered.isEmpty || pruned else { return }
        let before = values
        values["records"] = records + recovered
        values["pending_outputs"] = remaining
        do {
            try persist()
            for name in removable { unlinkat(dir, name, 0) }
        } catch {
            values = before
            values["records"] = records + recovered
        }
    }
    func completed(_ fingerprints: [String], directory: URL, registry: SavedDownloadRegistry, expectedNames: [String: String]? = nil) -> [[String: String]] {
        recoverPending(directory)
        let wanted = Set(fingerprints)
        var found: [[String: String]] = []
        for record in records.reversed() {
            guard let fingerprint = record["fingerprint"] as? String, wanted.contains(fingerprint),
                  expectedNames == nil || expectedNames?[fingerprint] == record["name"] as? String,
                  !found.contains(where: { $0["fingerprint"] == fingerprint }), let url = verified(record, directory: directory) else { continue }
            let id = registry.register(url)
            found.append(["fingerprint": fingerprint, "name": url.lastPathComponent, "path": url.path, "save_id": id])
        }
        return found
    }
    func prior(_ route: String, directory: URL, registry: SavedDownloadRegistry) -> [String: Any]? {
        recoverPending(directory)
        for record in records.reversed() where record["route"] as? String == route {
            guard let url = verified(record, directory: directory), let fingerprint = record["fingerprint"] as? String else { continue }
            return ["ok": true, "save_id": registry.register(url), "name": url.lastPathComponent, "path": url.path, "fingerprint": fingerprint]
        }
        return nil
    }
    static func validName(_ name: String) -> Bool { name.utf8.count <= 255 && name.range(of: "^[0-9]+\\.mp3$", options: .regularExpression) != nil }
    func conflicts(_ names: [String: String], completed: [[String: String]], directory: URL) -> [[String: String]] {
        let done = Set(completed.compactMap { $0["fingerprint"] })
        let dir = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard dir >= 0 else { return names.map { ["fingerprint": $0.key, "name": $0.value, "error": "目录不可用"] } }; defer { close(dir) }
        return names.compactMap { fp, name in
            var info = stat()
            guard !done.contains(fp), fstatat(dir, name, &info, AT_SYMLINK_NOFOLLOW) == 0 else { return nil }
            return ["fingerprint": fp, "name": name, "error": "同名文件已存在且无法核实，请移动旧文件或选择其他目录。"]
        }
    }
    // Legacy JSON writer retained for migration regressions, never invoked by
    // production MP3 saves. Active saves delegate to NativeWorkspaceAccess.
    func save(_ data: Data, route: String, fingerprint: String, filename: String, directory: URL, registry: SavedDownloadRegistry) throws -> [String: Any] {
        guard Self.validName(filename) else { throw SelectedXMLFolderError.unreadable }
        if let saved = completed([fingerprint], directory: directory, registry: registry, expectedNames: [fingerprint: filename]).first {
            var result: [String: Any] = saved.mapValues { $0 as Any }
            result["ok"] = true; result["resource_path"] = route
            return result
        }
        guard !data.isEmpty, data.count <= 256 * 1024 * 1024 else { throw SelectedXMLFolderError.fileTooLarge }
        let dir = open(directory.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard dir >= 0 else { throw SelectedXMLFolderError.unreadable }; defer { close(dir) }
        let temporary = ".dialogue-" + UUID().uuidString + ".tmp"
        let fd = openat(dir, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw SelectedXMLFolderError.unreadable }
        var keepTemporary = false
        defer { close(fd); if !keepTemporary { unlinkat(dir, temporary, 0) } }
        let written = data.withUnsafeBytes { raw -> Bool in
            guard let base = raw.baseAddress else { return false }
            var offset = 0
            while offset < raw.count {
                let n = Darwin.write(fd, base.advanced(by: offset), raw.count - offset)
                if n < 0 && errno == EINTR { continue }
                guard n > 0 else { return false }; offset += n
            }
            return true
        }
        guard written, fsync(fd) == 0 else { throw SelectedXMLFolderError.unreadable }
        let journalID = UUID().uuidString
        let journal: [String: Any] = ["journal_id": journalID, "temporary": temporary, "folder": directory.path, "route": route, "fingerprint": fingerprint, "size": data.count, "sha256": Self.hash(data)]
        let beforeJournal = values
        values["pending_outputs"] = (values["pending_outputs"] as? [[String: Any]] ?? []) + [journal]
        do { try persist(); keepTemporary = true } catch { values = beforeJournal; throw error }
        func abandonUnpublished() {
            values = beforeJournal
            try? persist()
            keepTemporary = false
        }
        do { try beforeLinkHook?() } catch { abandonUnpublished(); throw error }
        let name = filename
        guard linkat(dir, temporary, dir, name, 0) == 0 else {
            let code = errno
            abandonUnpublished()
            if code == EEXIST { throw SelectedXMLFolderError.nameConflict }
            throw code == ENOTSUP || code == ENOSYS ? SelectedXMLFolderError.unsupportedLinks : SelectedXMLFolderError.linkFailed
        }
        try afterLinkHook?()
        guard fsync(dir) == 0 else { throw SelectedXMLFolderError.unreadable }
        let record: [String: Any] = ["folder": directory.path, "route": route, "fingerprint": fingerprint, "name": name, "size": data.count, "sha256": Self.hash(data)]
        let pendingSnapshot = values
        values["records"] = records + [record]
        values["pending_outputs"] = (values["pending_outputs"] as? [[String: Any]] ?? []).filter { $0["journal_id"] as? String != journalID }
        var warning: String?
        do { try persist(); keepTemporary = false } catch {
            values = pendingSnapshot
            values["records"] = records + [record]
            warning = "音频已保存；完成记录将在下次核验时恢复。"
        }
        let url = directory.appendingPathComponent(name)
        var result: [String: Any] = ["ok": true, "save_id": registry.register(url), "name": name, "path": url.path, "fingerprint": fingerprint]
        if let warning = warning { result["warning"] = warning }
        return result
    }
    func preferences() -> [String: Any] {
        var result: [String: Any] = ["ok": true, "voices": values["voices"] ?? [:], "rate": values["rate"] ?? 1.0, "pause_ms": values["pause_ms"] ?? 250, "theme": values["theme"] ?? "light", "auto_voice": values["auto_voice"] ?? true, "manual_user_voice": values["manual_user_voice"] ?? false]
        if values["manual_user_voice"] == nil, let voices = values["voices"] as? [String: String], let user = voices["USER"], user != "zh-CN-XiaoyiNeural" { result["manual_user_voice"] = true; result["auto_voice"] = false }
        for role in ["input", "output"] {
            if let id = roles[role], let url = urls[id] {
                var isDirectory: ObjCBool = false
                let valid = !staleRoles.contains(role) && FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) && isDirectory.boolValue
                result[role + "_folder"] = ["folder_id": id, "path": url.path, "valid": valid]
            } else { result[role + "_folder"] = NSNull() }
        }
        return result
    }
    func setApplicationPreferences(_ args: [String: Any]) throws -> Bool {
        guard Set(args.keys) == Set(["theme"]), let theme = args["theme"] as? String, ["light", "dark"].contains(theme) else { return false }
        let previous = values
        values["theme"] = theme
        do { try persist(); return true } catch { values = previous; throw error }
    }
    func setPreferences(_ args: [String: Any]) throws -> Bool {
        guard let voices = args["voices"] as? [String: String], voices.count <= 2,
              Set(voices.keys).isSubset(of: ["AGENT", "USER"]), voices.values.allSatisfy({ allowedVoices.contains($0) }),
              let rate = args["rate"] as? Double, rate.isFinite, (0.5...2.0).contains(rate),
              let pause = args["pause_ms"] as? Int, (0...3000).contains(pause) else { return false }
        let theme = args["theme"] as? String ?? (values["theme"] as? String ?? "light")
        guard ["light", "dark"].contains(theme) else { return false }
        if let value = args["auto_voice"], !(value is Bool) { return false }
        if let value = args["manual_user_voice"], !(value is Bool) { return false }
        let auto = args["auto_voice"] as? Bool ?? values["auto_voice"] as? Bool ?? true
        let manual = args["manual_user_voice"] as? Bool ?? values["manual_user_voice"] as? Bool ?? false
        let before = values
        values["auto_voice"] = auto; values["manual_user_voice"] = manual
        values["theme"] = theme
        values["voices"] = voices; values["rate"] = rate; values["pause_ms"] = pause
        do { try persist(); return true } catch { values = before; throw error }
    }
}

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

    func register(_ url: URL) -> String { let id = UUID().uuidString; saved[id] = url; return id }

    func clear() { pending.removeAll(); saved.removeAll() }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler, URLSessionTaskDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var service: Process?
    private var baseURL: URL?
    private var closing = false
    private var checkingClose = false
    private var closeApproved = false
    private var updating = false
    private var offeredUpdate: String?
    private var offeredUpdateIdentity: String?
    private let savedDownloads = SavedDownloadRegistry()
    private let folderState: FolderState = {
        if let root = ProcessInfo.processInfo.environment["DIALOGUE_VERIFICATION_STATE_ROOT"], root.hasPrefix("/") {
            var info = stat()
            precondition(lstat(root, &info) == 0 && (info.st_mode & S_IFMT) == S_IFDIR && info.st_uid == getuid() && (info.st_mode & 0o077) == 0, "Verification profile must be private and user-owned")
            return FolderState(storeOverride: URL(fileURLWithPath: root).appendingPathComponent("preferences.json"))
        }
        let args = CommandLine.arguments
        if args.count == 3, args[1] == "--verification-state", args[2].hasPrefix("/") {
            return FolderState(storeOverride: URL(fileURLWithPath: args[2]).appendingPathComponent("preferences.json"))
        }
        return FolderState()
    }()
    private var downloadSources: [ObjectIdentifier: String] = [:]
    private var capturedDownloads: [ObjectIdentifier: String] = [:]
    private var captureDirectories: [ObjectIdentifier: URL] = [:]
    private var trustedDownloads = Set<ObjectIdentifier>()
    private var saveWaiters: [String: [([String: Any]) -> Void]] = [:]
    private let nativeToken = (UUID().uuidString + UUID().uuidString).replacingOccurrences(of: "-", with: "")
    private var workspaceSaves = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let item = NSMenuItem()
        let appMenu = NSMenu()
        let checkUpdateItem = NSMenuItem(title: "检查更新…", action: #selector(checkForUpdatesFromMenu(_:)), keyEquivalent: "")
        checkUpdateItem.target = self
        appMenu.addItem(checkUpdateItem)
        appMenu.addItem(.separator())
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
        config.userContentController.add(self, name: "dialogueFolders")
        config.userContentController.add(self, name: "dialogueUpdater")
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
        var environment = ProcessInfo.processInfo.environment
        environment["DIALOGUE_NATIVE_TOKEN"] = nativeToken
        environment["DIALOGUE_NATIVE_STATE_DIR"] = folderState.preferencesURL.deletingLastPathComponent().path
        process.environment = environment
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
                scheduleNativeTermination { NSApp.terminate(nil) }
            }
        }
        do { try process.run() } catch { showStartupError(); scheduleNativeTermination { NSApp.terminate(nil) }; return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
            guard let self = self, self.baseURL == nil, !self.closing else { return }
            self.showStartupError()
            scheduleNativeTermination { NSApp.terminate(nil) }
        }
    }

    @objc private func checkForUpdatesFromMenu(_ sender: Any?) {
        guard !closing else { return }
        guard !updating else { showMenuUpdateStatus("更新正在进行，请等待完成。"); return }
        guard let page = webView, isWorkbench(page.url), service?.isRunning == true else {
            showMenuUpdateStatus("工作台还未准备好，请稍后再试。"); return
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        page.evaluateJavaScript(updateMenuCheckScript) { [weak self] result, error in
            guard let self = self, !self.closing else { return }
            if error == nil, let route = result as? String {
                if route == "react" || route == "legacy" { return }
                if route == "busy" { self.showMenuUpdateStatus("正在检查或安装更新，请等待完成。"); return }
            }
            self.showMenuUpdateStatus("工作台还未准备好，请稍后再试。");
        }
    }

    private func showMenuUpdateStatus(_ message: String) {
        let alert = NSAlert(); alert.messageText = "检查更新"; alert.informativeText = message
        if let window = window { alert.beginSheetModal(for: window) }
        else { alert.runModal() }
    }

    private func showStartupError() {
        let alert = NSAlert()
        alert.messageText = "对白未能启动"
        alert.informativeText = "请确认应用文件完整后重试。原文件没有被修改。"
        alert.alertStyle = .critical
        alert.runModal()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func windowShouldClose(_ sender: NSWindow) -> Bool { if closeApproved { return true }; if !updating && !closing { NSApp.terminate(nil) }; return false }

    func windowWillClose(_ notification: Notification) { clearSaveState() }

    private func clearSaveState() {
        savedDownloads.clear()
        trustedDownloads.removeAll()
        downloadSources.removeAll()
        capturedDownloads.removeAll()
        captureDirectories.removeAll()
        saveWaiters.removeAll()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "dialogueReveal")
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "dialogueFolders")
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "dialogueUpdater")
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if updating || checkingClose { return .terminateCancel }
        if closing { return closeApproved ? .terminateNow : .terminateLater }
        guard let process = service, process.isRunning else { closeApproved = true; closing = true; clearSaveState(); return .terminateNow }
        checkingClose = true
        var answered = false
        let finish: ([String: Any]?) -> Void = { [weak self] safety in
            guard let self = self, !answered else { return }; answered = true
            self.checkingClose = false
            guard let safety = safety, safety["pending"] as? Bool == false,
                  let running = safety["running"] as? Bool, let saving = safety["saving"] as? Bool,
                  let unsaved = safety["unsaved"] as? Bool else {
                let alert = NSAlert(); alert.messageText = "暂时无法确认任务和保存状态"
                alert.informativeText = "请等待页面完成操作后再退出，已有结果保持可恢复。"; alert.runModal(); NSApp.reply(toApplicationShouldTerminate: false); return
            }
            if saving || !self.trustedDownloads.isEmpty || !self.saveWaiters.isEmpty || self.workspaceSaves > 0 {
                let alert = NSAlert(); alert.messageText = "正在保存音频"; alert.informativeText = "请等待保存完成后再退出。"; alert.runModal(); NSApp.reply(toApplicationShouldTerminate: false); return
            }
            if unsaved {
                let alert = NSAlert(); alert.messageText = "还有未保存的音频"
                alert.informativeText = running ? "退出将停止正在合成的任务，并丢弃未保存的临时音频。" : "退出后，未保存的临时音频将被清理。"
                alert.addButton(withTitle: "返回保存"); alert.addButton(withTitle: "放弃并退出"); alert.addButton(withTitle: "取消退出")
                let answer = alert.runModal()
                if answer == .alertFirstButtonReturn { NSApp.reply(toApplicationShouldTerminate: false); self.returnToUnsavedAudio(); return }
                guard answer == .alertSecondButtonReturn else { NSApp.reply(toApplicationShouldTerminate: false); return }
                self.checkingClose = true
                self.workspaceSaves += 1
                self.nativeRequest("/api/native/audio-captures/discard", [:]) { result in
                    self.workspaceSaves -= 1
                    guard result["ok"] as? Bool == true else {
                        self.checkingClose = false
                        self.showAudioSaveError("未能安全清理临时音频，请返回保存或稍后再试。")
                        NSApp.reply(toApplicationShouldTerminate: false); return
                    }
                    self.checkingClose = true
                    self.finishTermination(process, approvedPersistent: running || safety["persistent_unsaved"] as? Bool == true)
                }
                return
            } else if safety["persistent_unsaved"] as? Bool == true {
                let alert = NSAlert(); alert.messageText = "未保存的完整音频已缓存在本机"
                alert.informativeText = "退出会中断后台任务，但会保留进度和完整缓存。下次打开核验目录后，可重试保存或明确授权后继续生成。"
                alert.addButton(withTitle: "保存进度并退出"); alert.addButton(withTitle: "返回保存"); alert.addButton(withTitle: "取消退出")
                let answer = alert.runModal()
                if answer == .alertSecondButtonReturn { self.webView.evaluateJavaScript("window.dialogueShowUnsaved && window.dialogueShowUnsaved()", completionHandler: nil); NSApp.reply(toApplicationShouldTerminate: false); return }
                guard answer == .alertFirstButtonReturn else { NSApp.reply(toApplicationShouldTerminate: false); return }
            } else if running {
                let alert = NSAlert(); alert.messageText = "停止合成并退出？"; alert.informativeText = "正在运行的任务将停止。"
                if safety["persistent_workspace"] as? Bool == true { alert.informativeText = "任务进度保存在本机。下次打开后核验目录权限，并点击继续，才会重新联网合成。" }
                alert.addButton(withTitle: "停止并退出"); alert.addButton(withTitle: "取消退出")
                guard alert.runModal() == .alertFirstButtonReturn else { NSApp.reply(toApplicationShouldTerminate: false); return }
            }
            self.checkingClose = true
            self.finishTermination(process, approvedPersistent: running || safety["persistent_unsaved"] as? Bool == true)
        }
        webView.evaluateJavaScript("window.dialogueCloseSafety && window.dialogueCloseSafety()") { result, error in
            guard error == nil, let state = result as? [String: Any] else { finish(nil); return }
            self.nativeRequest("/api/native/workspaces/safety", [:], timeout: 3) { backend in
                finish(mergeNativeCloseSafety(state, backend))
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { finish(nil) }
        // AppKit resumes only after the page reply, prompt and service shutdown.
        return .terminateLater
    }

    private func finishTermination(_ process: Process, approvedPersistent: Bool) {
        nativeRequest("/api/native/workspaces/safety", [:], timeout: 3) { [weak self] backend in
            guard let self = self else { return }
            let idle: [String: Any] = ["running": false, "saving": false, "pending": false, "unsaved": false]
            guard let state = mergeNativeCloseSafety(idle, backend), state["pending"] as? Bool == false,
                  state["saving"] as? Bool == false, self.workspaceSaves == 0,
                  self.trustedDownloads.isEmpty, self.saveWaiters.isEmpty,
                  approvedPersistent || (state["running"] as? Bool == false && state["persistent_unsaved"] as? Bool == false) else {
                let alert = NSAlert(); alert.messageText = "任务或保存状态发生变化"
                alert.informativeText = "请等待当前保存完成，确认全部任务后再退出。已有进度和完整缓存会保留。"
                self.checkingClose = false
                alert.runModal(); NSApp.reply(toApplicationShouldTerminate: false); return
            }
            self.checkingClose = false; self.closing = true; self.clearSaveState(); process.terminate()
            DispatchQueue.global(qos: .userInitiated).async {
                let deadline = Date().addingTimeInterval(6)
                while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
                if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                process.waitUntilExit()
                DispatchQueue.main.async { self.closeApproved = true; NSApp.reply(toApplicationShouldTerminate: true) }
            }
        }
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
        guard !closing, message.webView === webView,
              message.frameInfo.isMainFrame, origin.`protocol` == "http", origin.host == "127.0.0.1",
              origin.port == baseURL?.port, isWorkbench(message.frameInfo.request.url), isWorkbench(webView.url) else { return }
        if message.name == "dialogueFolders" { if !updating { handleFolderMessage(message.body) }; return }
        if message.name == "dialogueUpdater" { handleUpdaterMessage(message.body); return }
        guard message.name == "dialogueReveal" else { return }
        guard let url = savedDownloads.target(for: message.body) else {
            notifyPage("dialogueNativeRevealResult", payload: ["ok": false])
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([url])
        notifyPage("dialogueNativeRevealResult", payload: ["ok": true])
    }

    private func nativeRequest(_ route: String, _ payload: [String: Any], timeout: TimeInterval = 60, completion: @escaping ([String: Any]) -> Void) {
        guard let base = baseURL, route.hasPrefix("/api/native/"),
              let target = URL(string: route, relativeTo: base)?.absoluteURL, isLocal(target),
              let data = try? JSONSerialization.data(withJSONObject: payload) else { completion(["ok": false, "error": "原生服务未就绪"]); return }
        var request = URLRequest(url: target)
        request.httpMethod = "POST"; request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(nativeToken, forHTTPHeaderField: "X-Native-Token")
        request.httpBody = data
        let session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
        session.dataTask(with: request) { bytes, response, error in
            defer { session.finishTasksAndInvalidate() }
            var result: [String: Any] = ["ok": false, "error": "目录服务请求失败，请重试"]
            if error == nil, let response = response as? HTTPURLResponse, response.statusCode == 200,
               let bytes = bytes, bytes.count <= 1024 * 1024,
               let decoded = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] { result = decoded }
            DispatchQueue.main.async { [weak self] in
                // A successful private request proves the service has opened and
                // migrated this profile. Keep preference writes small thereafter.
                if result["ok"] as? Bool != false, result["error"] == nil { try? self?.folderState.compactMigratedReceipts() }
                completion(result)
            }
        }.resume()
    }

    private func workspaceFolder(_ id: String, role: String, completion: @escaping ([String: Any]) -> Void) {
        if let directory = folderState.folder(id, role: role) {
            nativeRequest("/api/native/folders", ["kind": role, "path": directory.path], completion: completion)
        } else {
            nativeRequest("/api/native/folders/info", ["kind": role, "folder_id": id], completion: completion)
        }
    }

    private func adoptReceipt(_ receipt: [String: Any]) -> [String: Any] {
        var result = receipt
        if receipt["ok"] as? Bool == true, let path = receipt["path"] as? String {
            result["save_id"] = savedDownloads.register(URL(fileURLWithPath: path))
        }
        return result
    }

    private func saveWithLedger(_ route: String, folderID: String?, directory: URL? = nil, reply: @escaping ([String: Any]) -> Void) {
        workspaceSaves += 1
        let registered: ([String: Any]) -> Void = { [weak self] folder in
            guard let self = self else { return }
            guard folder["ok"] as? Bool != false, let id = folder["folder_id"] as? String else { self.workspaceSaves -= 1; reply(folder); return }
            let finish: ([String: Any]) -> Void = { result in
                self.workspaceSaves -= 1
                let receipt = self.adoptReceipt(result); reply(receipt)
                if receipt["ok"] as? Bool == true { self.notifyPage("dialogueNativeSaved", payload: receipt) }
            }
            self.nativeRequest("/api/native/audio-captures/find", ["resource_path": route]) { lookup in
                guard lookup["ok"] as? Bool == true else { finish(lookup); return }
                if let capture = lookup["capture"] as? [String: Any], let captureID = capture["capture_id"] as? String {
                    self.nativeRequest("/api/native/audio-captures/save", ["capture_id": captureID, "folder_id": id], completion: finish)
                } else {
                    self.nativeRequest("/api/native/outputs/save", ["folder_id": id, "resource_path": route], completion: finish)
                }
            }
        }
        if let directory = directory { nativeRequest("/api/native/folders", ["kind": "output", "path": directory.path], completion: registered) }
        else if let id = folderID { workspaceFolder(id, role: "output", completion: registered) }
        else { workspaceSaves -= 1; reply(["ok": false, "error": "请选择结果目录"]) }
    }

    private func showAudioSaveError(_ message: String) {
        let alert = NSAlert(); alert.messageText = "音频尚未保存"; alert.informativeText = message
        alert.beginSheetModal(for: window)
    }

    private func saveCapturedAudio(_ captureID: String, directory: URL) {
        workspaceSaves += 1
        nativeRequest("/api/native/folders", ["kind": "output", "path": directory.path]) { [weak self] folder in
            guard let self = self else { return }
            guard folder["ok"] as? Bool != false, let folderID = folder["folder_id"] as? String else {
                self.workspaceSaves -= 1; self.showAudioSaveError("结果目录不可用，完整音频仍保留在本机。"); return
            }
            self.nativeRequest("/api/native/audio-captures/save", ["capture_id": captureID, "folder_id": folderID]) { result in
                self.workspaceSaves -= 1
                if result["ok"] as? Bool == true {
                    self.notifyPage("dialogueNativeSaved", payload: self.adoptReceipt(result))
                } else { self.showAudioSaveError(result["error"] as? String ?? "保存失败，完整音频仍保留在本机。") }
            }
        }
    }

    private func chooseCapturedAudioDestination(_ capture: [String: Any]) {
        guard let ident = capture["capture_id"] as? String, ident.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
              let name = capture["filename"] as? String, FolderState.validName(name) else { showAudioSaveError("原生音频信息无法确认。"); return }
        let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        panel.message = "保存已下载的 \(name)。取消后完整缓存仍会保留。"
        panel.beginSheetModal(for: window) { [weak self] answer in
            guard let self = self, !self.closing, answer == .OK, let directory = panel.url else { return }
            self.saveCapturedAudio(ident, directory: directory)
        }
    }

    private func returnToUnsavedAudio() {
        workspaceSaves += 1
        nativeRequest("/api/native/audio-captures/pending", [:]) { [weak self] result in
            guard let self = self else { return }
            self.workspaceSaves -= 1
            guard result["ok"] as? Bool == true, let captures = result["captures"] as? [[String: Any]], captures.count <= 128 else {
                self.showAudioSaveError("无法确认本机待保存音频，请稍后重试。"); return
            }
            if let first = captures.first(where: { $0["state"] as? String == "ready" }) { self.chooseCapturedAudioDestination(first) }
            else if !captures.isEmpty { self.showAudioSaveError("下载未完整完成或缓存已变化，不能直接保存。请取消退出后重新生成，或退出时明确放弃这些临时缓存。") }
            else { self.webView.evaluateJavaScript("window.dialogueShowUnsaved && window.dialogueShowUnsaved()", completionHandler: nil) }
        }
    }

    private func saveWorkspaceReport(_ reportID: String, reply: @escaping ([String: Any]) -> Void) {
        workspaceSaves += 1
        nativeRequest("/api/native/reports/info", ["report_id": reportID]) { [weak self] info in
            guard let self = self else { return }
            guard info["ok"] as? Bool != false, let name = info["filename"] as? String,
                  name.lowercased().hasSuffix(".csv"), !name.contains("/"), !name.contains("\\"), !name.contains("\0") else {
                self.workspaceSaves -= 1
                reply(["ok": false, "error": "清单尚未准备好，请重新导出"]); return
            }
            let panel = NSSavePanel()
            panel.nameFieldStringValue = name
            panel.allowedContentTypes = [.commaSeparatedText]
            panel.allowsOtherFileTypes = false
            panel.canCreateDirectories = true
            panel.message = "保存当前批次的完整清单。每份源 XML 一行，不包含正文或完整目录路径。已有文件不会覆盖。"
            panel.beginSheetModal(for: self.window) { [weak self] response in
                guard let self = self else { return }
                guard response == .OK, let target = panel.url else {
                    self.workspaceSaves -= 1; reply(["cancelled": true]); return
                }
                guard target.pathExtension.lowercased() == "csv" else {
                    self.workspaceSaves -= 1; reply(["ok": false, "error": "请选择新的 CSV 文件"]); return
                }
                self.nativeRequest("/api/native/reports/save", ["report_id": reportID, "path": target.path]) { result in
                    self.workspaceSaves -= 1
                    reply(result)
                }
            }
        }
    }

    private func handleFolderMessage(_ body: Any) {
        guard let message = body as? [String: Any], let requestID = message["request_id"] as? String,
              requestID.count <= 128, let method = message["method"] as? String,
              let args = message["args"] as? [String: Any] else { return }
        let reply: ([String: Any]) -> Void = { [weak self] result in
            self?.notifyPage("dialogueNativeFolderResponse", payload: ["request_id": requestID, "result": result])
        }
        switch method {
        case "request_close": reply(["ok": true]); scheduleNativeTermination { NSApp.terminate(nil) }
        case "get_preferences": reply(folderState.preferences())
        case "set_preferences":
            do { reply(try folderState.setPreferences(args) ? ["ok": true] : ["ok": false, "error": "设置无效"]) }
            catch { reply(["ok": false, "error": "无法保存设置"]) }
        case "set_application_preferences":
            do { reply(try folderState.setApplicationPreferences(args) ? ["ok": true] : ["ok": false, "error": "应用设置无效"]) }
            catch { reply(["ok": false, "error": "无法保存应用设置"]) }
        case "choose_workspace_input", "choose_workspace_output":
            let role = method == "choose_workspace_input" ? "input" : "output"
            let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.beginSheetModal(for: window) { [weak self] response in
                guard let self = self, !self.closing else { return }
                guard response == .OK, let directory = panel.url else { reply(["cancelled": true]); return }
                self.nativeRequest("/api/native/folders", ["kind": role, "path": directory.path]) { result in
                    guard result["ok"] as? Bool != false else { reply(result); return }
                    do { _ = try self.folderState.choose(directory, role: role); var chosen = result; chosen["ok"] = true; reply(chosen) }
                    catch { reply(["ok": false, "error": "目录偏好未能保存，请重试"]) }
                }
            }
        case "restore_workspace_access":
            guard let id = args["workspace_id"] as? String, id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil else { reply(["ok": false, "error": "任务标识无效"]); return }
            nativeRequest("/api/native/workspaces/restore", ["workspace_id": id], completion: reply)
        case "choose_input_directory", "choose_output_directory":
            let role = method == "choose_input_directory" ? "input" : "output"
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.beginSheetModal(for: window) { [weak self] response in
                guard let self = self, !self.closing else { return }
                guard response == .OK, let url = panel.url else { reply(["cancelled": true]); return }
                do {
                    let files = role == "input" ? try readSelectedXMLFolder(url) : []
                    var result = try self.folderState.choose(url, role: role)
                    if role == "input" { result["files"] = files }
                    reply(result)
                } catch { reply(["ok": false, "error": "无法读取或保存所选文件夹，请检查 XML 数量、大小和权限。 "]) }
            }
        case "refresh_input_directory":
            guard let id = args["folder_id"] as? String, let url = folderState.folder(id, role: "input") else { reply(["ok": false, "error": "文件夹授权无效"]); return }
            do { reply(["ok": true, "folder_id": id, "path": url.path, "files": try readSelectedXMLFolder(url)]) }
            catch { reply(["ok": false, "error": "无法读取文件夹，请检查 XML 数量、大小和权限。 "]) }
        case "check_output_space":
            guard let id = args["folder_id"] as? String else { reply(["ok": false, "error": "文件夹授权无效"]); return }
            workspaceFolder(id, role: "output") { result in
                guard result["ok"] as? Bool != false, let path = result["path"] as? String else { reply(result); return }
                let url = URL(fileURLWithPath: path), probe = URL(fileURLWithPath: path).appendingPathComponent(".dialogue-check-" + UUID().uuidString)
                let fd = open(probe.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
                guard fd >= 0 else { reply(["ok": false, "error": "无法写入结果文件夹，请重新选择"]); return }
                close(fd); unlink(probe.path)
                let free = (try? url.resourceValues(forKeys: [.volumeAvailableCapacityKey]).volumeAvailableCapacity).flatMap { $0 }
                reply(["ok": true, "writable": true, "free_bytes": free.map { $0 as Any } ?? NSNull()])
            }
        case "check_completed":
            guard let id = args["folder_id"] as? String,
                  let fingerprints = args["fingerprints"] as? [String], fingerprints.count <= 50,
                  fingerprints.allSatisfy({ $0.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil }) else { reply(["ok": false, "error": "完成记录请求无效"]); return }
            let names = args["expected_names"] as? [String: String]
            guard args["expected_names"] == nil || (names != nil && Set(names!.keys) == Set(fingerprints) && names!.values.allSatisfy(FolderState.validName)) else { reply(["ok": false, "error": "预期名称无效"]); return }
            workspaceFolder(id, role: "output") { [weak self] folder in
                guard let self = self, folder["ok"] as? Bool != false, let grant = folder["folder_id"] as? String else { reply(folder); return }
                var payload: [String: Any] = ["folder_id": grant, "fingerprints": fingerprints]
                if let names = names { payload["expected_names"] = names }
                self.nativeRequest("/api/native/outputs/check", payload) { result in
                    var adopted = result
                    if let items = result["completed"] as? [[String: Any]] { adopted["completed"] = items.map { self.adoptReceipt($0) } }
                    reply(adopted)
                }
            }
        case "save_workspace_report":
            guard let reportID = args["report_id"] as? String,
                  reportID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
                  Set(args.keys) == Set(["report_id"]) else { reply(["ok": false, "error": "清单请求无效"]); return }
            saveWorkspaceReport(reportID, reply: reply)
        case "save_to_output":
            guard let id = args["folder_id"] as? String,
                  let route = args["resource_path"] as? String, route.range(of: "^/api/(jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/audio/[0-9]{3})$", options: .regularExpression) != nil else { reply(["ok": false, "error": "输出请求无效"]); return }
            saveWithLedger(route, folderID: id, reply: reply)
        case "save_download":
            guard let route = args["resource_path"] as? String, route.range(of: "^/api/(jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/audio/[0-9]{3})$", options: .regularExpression) != nil else { reply(["ok": false, "error": "输出请求无效"]); return }
            let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.message = "选择保存文件夹；MP3 名称由两份 XML 确定。"
            panel.beginSheetModal(for: window) { [weak self] response in
                guard let self = self, !self.closing else { return }
                guard response == .OK, let directory = panel.url else { reply(["cancelled": true]); return }
                self.saveWithLedger(route, folderID: nil, directory: directory, reply: reply)
            }
        case "reveal_output":
            guard let id = args["folder_id"] as? String else { reply(["ok": false, "error": "文件夹授权无效"]); return }
            workspaceFolder(id, role: "output") { result in
                guard result["ok"] as? Bool != false, let path = result["path"] as? String else { reply(result); return }
                reply(["ok": NSWorkspace.shared.open(URL(fileURLWithPath: path))])
            }
        default: reply(["ok": false, "error": "该功能尚未完成"])
        }
    }

    private func performAudioSave(_ route: String, directory: URL, id: String?, reply: @escaping ([String: Any]) -> Void) {
        // Historical JSON-writer adapter retained only for migration regressions.
        // Production MP3 downloads use capture IDs and the canonical SQLite writer.
        guard let base = baseURL, let target = URL(string: route, relativeTo: base)?.absoluteURL, isLocal(target) else { reply(["ok": false, "error": "输出请求无效"]); return }
            let key = directory.path + "|" + route
            if saveWaiters[key] != nil { saveWaiters[key]?.append(reply); return }
            saveWaiters[key] = [reply]
            let finish: ([String: Any]) -> Void = { [weak self] result in
                guard let self = self else { return }
                let callbacks = self.saveWaiters.removeValue(forKey: key) ?? []
                callbacks.forEach { $0(result) }
            }
            let session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
            session.downloadTask(with: target) { [weak self] location, response, error in
                defer { session.finishTasksAndInvalidate() }
                guard error == nil, let location = location, let response = response as? HTTPURLResponse,
                      response.statusCode == 200, response.mimeType == "audio/mpeg", response.expectedContentLength > 0,
                      response.expectedContentLength <= 256 * 1024 * 1024,
                      let fingerprint = response.value(forHTTPHeaderField: "X-Dialogue-Fingerprint"),
                      let disposition = response.value(forHTTPHeaderField: "Content-Disposition"),
                      disposition.hasPrefix("attachment; filename=\""), disposition.hasSuffix("\""),
                      FolderState.validName(String(disposition.dropFirst(22).dropLast())),
                      fingerprint.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
                      let data = try? Data(contentsOf: location), data.count == response.expectedContentLength else {
                    DispatchQueue.main.async { finish(["ok": false, "error": "获取音频失败，请重试。 "]) }; return
                }
                DispatchQueue.main.async {
                    guard let self = self, !self.closing, (id == nil || self.folderState.folder(id!, role: "output") == directory) else { finish(["ok": false, "error": "文件夹授权已失效"]); return }
                    do {
                        let saved = try self.folderState.save(data, route: route, fingerprint: fingerprint, filename: String(disposition.dropFirst(22).dropLast()), directory: directory, registry: self.savedDownloads)
                        finish(saved)
                        if let warning = saved["warning"] as? String, let path = saved["path"] as? String {
                            let alert = NSAlert(); alert.messageText = "音频已保存"; alert.informativeText = warning + "\n" + path
                            alert.beginSheetModal(for: self.window)
                        }
                    } catch SelectedXMLFolderError.nameConflict {
                        finish(["ok": false, "conflict": true, "error": "同名文件已存在，请处理冲突或选择其他目录后重试保存。 "])
                    } catch SelectedXMLFolderError.unsupportedLinks {
                        finish(["ok": false, "error": "该文件系统不支持当前安全保存方式，请选择其他文件夹。"])
                        let alert = NSAlert(); alert.messageText = "无法保存到此文件夹"; alert.informativeText = "该文件系统不支持当前安全保存方式，请选择其他文件夹。"; alert.beginSheetModal(for: self.window)
                    } catch SelectedXMLFolderError.linkFailed {
                        finish(["ok": false, "error": "无法通过安全链接保存；文件系统可能不支持、权限不足或空间不足。"])
                        let alert = NSAlert(); alert.messageText = "无法保存到此文件夹"; alert.informativeText = "无法通过安全链接保存；文件系统可能不支持、权限不足或空间不足，请选择其他文件夹。"; alert.beginSheetModal(for: self.window)
                    } catch SelectedXMLFolderError.namesExhausted {
                        finish(["ok": false, "error": "同名文件数量已达上限，请选择其他文件夹。"])
                        let alert = NSAlert(); alert.messageText = "无法保存到此文件夹"; alert.informativeText = "同名文件数量已达上限，请选择其他文件夹。"; alert.beginSheetModal(for: self.window)
                    } catch { finish(["ok": false, "error": "保存失败，请检查文件夹权限和剩余空间。 "]) }
                }
            }.resume()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }

    private func runUpdaterCLI(_ arguments: [String], timeout: TimeInterval? = nil, completion: @escaping ([String: Any]) -> Void) {
        var returned = false
        let finish: ([String: Any]) -> Void = { result in
            guard !returned else { return }; returned = true; completion(result)
        }
        if let timeout = timeout { DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { finish(["state": "error", "message": "更新操作超时"]) } }
        let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/MacOS/DialogueTTSService")
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process(); let pipe = Pipe()
            process.executableURL = executable; process.arguments = arguments
            process.standardOutput = pipe; process.standardError = FileHandle.nullDevice
            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let result = (data.count <= 65536 ? (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) : nil) ?? ["state": "error", "message": "更新服务返回无效结果"]
                DispatchQueue.main.async { finish(result) }
            } catch { DispatchQueue.main.async { finish(["state": "error", "message": "无法启动更新服务"]) } }
        }
    }
    private func handleUpdaterMessage(_ body: Any) {
        guard let message = body as? [String: Any], let id = message["request_id"] as? String, id.count <= 128,
              let method = message["method"] as? String, let args = message["args"] as? [String: Any], args.isEmpty else { return }
        let reply: ([String: Any]) -> Void = { [weak self] result in
            self?.notifyPage("dialogueNativeUpdateResponse", payload: ["request_id": id, "result": result])
        }
        guard let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String else {
            reply(["state": "error", "message": "无法读取当前版本"]); return
        }
        if method == "install_update" { installUpdate(version, reply: reply); return }
        guard method == "check_for_update", !updating else { reply(["state": "error", "message": "更新正在进行"]); return }
        offeredUpdate = nil
        offeredUpdateIdentity = nil
        runUpdaterCLI(["--update-check", version]) { [weak self] result in
            if result["state"] as? String == "available", let candidate = result["latest_version"] as? String,
               let identity = result["identity"] as? String, identity.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
               candidate.range(of: "^[0-9]+\\.[0-9]+\\.[0-9]+$", options: .regularExpression) != nil { self?.offeredUpdate = candidate; self?.offeredUpdateIdentity = identity }
            var publicResult = result; publicResult.removeValue(forKey: "identity"); reply(publicResult)
        }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard !closing, isWorkbench(webView.url), service?.isRunning == true else { return }
        let args = CommandLine.arguments
        guard args.count == 4, args[1] == "--update-receipt", !args[2].isEmpty, !args[3].isEmpty else { return }
        runUpdaterCLI(["--ack-update", args[2], args[3]]) { _ in }
    }

    private func showUpdateBlocked(_ message: String) {
        let alert = NSAlert(); alert.messageText = "更新暂未完成"; alert.informativeText = message
        alert.beginSheetModal(for: window)
    }

    private func postUpdateLock(_ lock: Bool, completion: @escaping (Bool) -> Void) {
        guard let base = baseURL, let config = URL(string: "/api/config", relativeTo: base)?.absoluteURL,
              let target = URL(string: lock ? "/api/update/lock" : "/api/update/unlock", relativeTo: base)?.absoluteURL,
              isLocal(config), isLocal(target) else { completion(false); return }
        let session = URLSession(configuration: .ephemeral, delegate: self, delegateQueue: nil)
        session.dataTask(with: config) { data, response, error in
            guard error == nil, let response = response as? HTTPURLResponse, response.statusCode == 200,
                  let data = data, data.count <= 65536,
                  let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let token = payload["token"] as? String, !token.isEmpty else {
                session.finishTasksAndInvalidate(); DispatchQueue.main.async { completion(false) }; return
            }
            var request = URLRequest(url: target); request.httpMethod = "POST"
            request.setValue(token, forHTTPHeaderField: "X-App-Token")
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = Data("{}".utf8)
            session.dataTask(with: request) { data, response, error in
                defer { session.finishTasksAndInvalidate() }
                let body = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                let ok = error == nil && (response as? HTTPURLResponse)?.statusCode == 200 && body?["ok"] as? Bool == true
                DispatchQueue.main.async { completion(ok) }
            }.resume()
        }.resume()
    }
    private func installUpdate(_ version: String, reply: @escaping ([String: Any]) -> Void) {
        guard !updating, let offered = offeredUpdate, let identity = offeredUpdateIdentity, let runningService = service, runningService.isRunning,
              trustedDownloads.isEmpty, saveWaiters.isEmpty, workspaceSaves == 0 else { reply(["state": "error", "message": "请先完成任务并保存所有结果，再检查更新。"]); return }
        updating = true
        webView.evaluateJavaScript("window.dialogueUpdateSafety && window.dialogueUpdateSafety()") { [weak self] result, error in
            guard let self = self, !self.closing else { return }
            guard error == nil, let safety = result as? [String: Any], safety["busy"] as? Bool == false,
                  safety["unsaved"] as? Bool == false, self.trustedDownloads.isEmpty, self.saveWaiters.isEmpty else {
                self.updating = false; reply(["state": "error", "message": "请先完成任务并保存所有音频。"]); return
            }
            let alert = NSAlert(); alert.messageText = "安装更新 \(offered)？"
            alert.informativeText = "将下载并校验更新，然后重启应用。原有设置和结果文件夹会保留。"
            alert.addButton(withTitle: "安装并重启"); alert.addButton(withTitle: "取消")
            alert.beginSheetModal(for: self.window) { answer in
                guard answer == .alertFirstButtonReturn else { self.updating = false; reply(["state": "cancelled"]); return }
                self.postUpdateLock(true) { locked in
                    guard locked else {
                        self.postUpdateLock(false) { unlocked in
                            guard unlocked else { self.showUpdateBlocked("无法确认任务锁已解除，请保持应用打开并联系支持。"); return }
                            self.updating = false; reply(["state": "error", "message": "无法安全锁定任务，请稍后重试。"]) }; return
                    }
                    self.webView.evaluateJavaScript("window.dialogueUpdateLock && window.dialogueUpdateLock(true)", completionHandler: nil)
                    let arguments = ["--update-install", Bundle.main.bundleURL.path, version, offered, String(ProcessInfo.processInfo.processIdentifier), String(runningService.processIdentifier), identity]
                    self.runUpdaterCLI(arguments) { result in
                        guard result["state"] as? String == "waiting_for_exit" else {
                            self.postUpdateLock(false) { unlocked in
                                guard unlocked else { self.showUpdateBlocked("无法确认任务锁已解除，请保持应用打开并联系支持。"); return }
                                self.updating = false; self.webView.evaluateJavaScript("window.dialogueUpdateLock && window.dialogueUpdateLock(false)", completionHandler: nil)
                                reply(["state": "error", "message": "更新未完成，请稍后重试。 "])
                            }; return
                        }
                        guard let transaction = result["transaction_id"] as? String,
                              transaction.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
                              let token = result["cancel_token"] as? String,
                              token.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
                            self.showUpdateBlocked("更新助手返回了不完整的事务信息；请保持应用打开并联系支持。")
                            return
                        }
                        reply(["state": "waiting_for_exit"])
                        runningService.terminate()
                        DispatchQueue.global(qos: .userInitiated).async {
                            let deadline = Date().addingTimeInterval(6)
                            while runningService.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
                            DispatchQueue.main.async {
                                if !runningService.isRunning { self.updating = false; scheduleNativeTermination { NSApp.terminate(nil) }; return }
                                self.runUpdaterCLI(["--cancel-update", transaction, token], timeout: 15) { cancellation in
                                    guard cancellation["state"] as? String == "cancelled", cancellation["cancelled"] as? Bool == true else {
                                        self.showUpdateBlocked("无法确认撤销更新；请保持应用打开并联系支持。")
                                        return
                                    }
                                    if !runningService.isRunning { self.updating = false; scheduleNativeTermination { NSApp.terminate(nil) }; return }
                                    self.postUpdateLock(false) { unlocked in
                                        if !runningService.isRunning { self.updating = false; scheduleNativeTermination { NSApp.terminate(nil) }; return }
                                        guard unlocked else { self.showUpdateBlocked("更新已撤销，但任务锁尚未解除；请保持应用打开并联系支持。 "); return }
                                        self.updating = false
                                        self.webView.evaluateJavaScript("window.dialogueUpdateLock && window.dialogueUpdateLock(false); if (document.getElementById('updatestatus')) document.getElementById('updatestatus').textContent='旧服务未正常退出，更新已安全取消。'", completionHandler: nil)
                                        self.showUpdateBlocked("旧服务未能正常退出，已安全取消更新。")
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private func isRelease(_ url: URL) -> Bool {
        let path = url.path.removingPercentEncoding ?? url.path
        return url.scheme == "https" && url.host == "github.com" && url.port == nil && url.user == nil && url.password == nil
            && url.query == nil && url.fragment == nil && path.hasPrefix("/Harry6400/dialogue-tts/releases/")
            && !path.split(separator: "/").contains("..") && !path.contains("\\")
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if isLocal(url) {
            if action.shouldPerformDownload, url.path.range(of: "^/api/(jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/audio/[0-9]{3})$", options: .regularExpression) != nil {
                workspaceSaves += 1
                nativeRequest("/api/native/audio-captures/find", ["resource_path": url.path]) { [weak self] lookup in
                    guard let self = self else { decisionHandler(.cancel); return }
                    self.workspaceSaves -= 1
                    guard !self.closing, lookup["ok"] as? Bool == true else { decisionHandler(.cancel); self.showAudioSaveError("无法确认音频缓存，请稍后重试。"); return }
                    if let capture = lookup["capture"] as? [String: Any] {
                        decisionHandler(.cancel)
                        self.chooseCapturedAudioDestination(capture)
                    } else { decisionHandler(.download) }
                }
            } else { decisionHandler(action.shouldPerformDownload ? .download : .allow) }
            return
        }
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
        guard !closing, !updating, let source = source, isLocal(source), isWorkbench(webView.url) else {
            download.cancel { _ in }
            return
        }
        let id = ObjectIdentifier(download)
        trustedDownloads.insert(id)
        downloadSources[id] = source.path
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
        if source.path.range(of: "^/api/(jobs/[a-f0-9]{32}/audio|batches/[a-f0-9]{32}/audio/[0-9]{3})$", options: .regularExpression) != nil {
            guard let metadata = trustedNativeAudioMetadata(response) else {
                trustedDownloads.remove(id); downloadSources.removeValue(forKey: id)
                completionHandler(nil); showAudioSaveError("可信音频响应不完整，未创建保存回执。"); return
            }
            let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.beginSheetModal(for: window) { [weak self] result in
                guard let self = self else { completionHandler(nil); return }
                guard !self.closing, result == .OK, let directory = panel.url else {
                    self.trustedDownloads.remove(id); self.downloadSources.removeValue(forKey: id); completionHandler(nil); return
                }
                self.nativeRequest("/api/native/audio-captures/reserve", metadata) { reserved in
                    guard reserved["ok"] as? Bool == true, let captureID = reserved["capture_id"] as? String,
                          captureID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil, let path = reserved["path"] as? String else {
                        self.trustedDownloads.remove(id); self.downloadSources.removeValue(forKey: id); completionHandler(nil)
                        self.showAudioSaveError(reserved["error"] as? String ?? "无法创建受保护的音频缓存。"); return
                    }
                    let captureURL = URL(fileURLWithPath: path).standardizedFileURL
                    let expected = self.folderState.preferencesURL.deletingLastPathComponent().appendingPathComponent("native-audio", isDirectory: true).appendingPathComponent(captureID, isDirectory: true).appendingPathComponent("audio.mp3").standardizedFileURL
                    guard !self.closing, captureURL == expected else {
                        self.trustedDownloads.remove(id); self.downloadSources.removeValue(forKey: id)
                        self.nativeRequest("/api/native/audio-captures/abort", ["capture_id": captureID]) { _ in }
                        completionHandler(nil); return
                    }
                    self.capturedDownloads[id] = captureID; self.captureDirectories[id] = directory
                    completionHandler(captureURL)
                }
            }
            return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] result in
            guard let self = self, !self.closing else { completionHandler(nil); return }
            let url = result == .OK ? panel.url : nil
            self.savedDownloads.selected(url, for: id)
            if url == nil { self.trustedDownloads.remove(id); self.downloadSources.removeValue(forKey: id) }
            completionHandler(url)
        }
    }

    func downloadDidFinish(_ download: WKDownload) {
        let id = ObjectIdentifier(download)
        guard trustedDownloads.remove(id) != nil else { return }
        let sourcePath = downloadSources.removeValue(forKey: id)
        if let captureID = capturedDownloads.removeValue(forKey: id) {
            let directory = captureDirectories.removeValue(forKey: id)
            workspaceSaves += 1
            nativeRequest("/api/native/audio-captures/complete", ["capture_id": captureID]) { [weak self] completed in
                guard let self = self else { return }
                self.workspaceSaves -= 1
                guard completed["ok"] as? Bool == true, let directory = directory else {
                    self.showAudioSaveError(completed["error"] as? String ?? "音频缓存未完整，未生成保存回执。"); return
                }
                self.saveCapturedAudio(captureID, directory: directory)
            }
            return
        }
        if var record = savedDownloads.completed(id) {
            if let sourcePath = sourcePath, sourcePath.range(of: "^/api/(jobs/[a-zA-Z0-9_-]{1,100}/audio|batches/[a-zA-Z0-9_-]{1,100}/(zip|audio/[0-9]{3}))$", options: .regularExpression) != nil { record["resource_path"] = sourcePath }
            notifyPage("dialogueNativeSaved", payload: record)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let id = ObjectIdentifier(download)
        trustedDownloads.remove(id)
        downloadSources.removeValue(forKey: id)
        captureDirectories.removeValue(forKey: id)
        if let captureID = capturedDownloads.removeValue(forKey: id) {
            workspaceSaves += 1
            nativeRequest("/api/native/audio-captures/abort", ["capture_id": captureID]) { [weak self] _ in self?.workspaceSaves -= 1 }
        }
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
