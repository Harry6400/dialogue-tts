# v0.3 native updater integration

Fixed GitHub repository Harry6400/dialogue-tts; Gitee distribution repository yongchu-64/dialogue-tts. No JS URL/path/command parameters.

## UI contract (implemented in index.html)
- Windows `window.pywebview.api.check_for_update()` / `install_update()`.
- Mac WK handler `dialogueUpdater`, main-frame/exact local origin only:
  `{request_id,method:"check_for_update"|"install_update",args:{}}`.
- Reply `window.dialogueNativeUpdateResponse({request_id,result})` via JSON encoding.
- Check returns `{state:"latest"|"available"|"error",current_version,latest_version,message?}`.
- Install returns `{state:"waiting_for_exit"|"cancelled"|"error",message?}`.
- `window.dialogueUpdateSafety()` returns `{busy,unsaved}`. Native MUST reject both.
- `window.dialogueUpdateLock(true|false)` prevents interaction; NOT a substitute for backend/native locks.
- Actual native successful manual save callback `dialogueNativeSaved(record)` includes `resource_path` of saved `/api/.../audio` or `/api/batches/.../zip`, in addition to existing save_id/name/path. Only successful save receipt marks audio safe. Automatic folder output is tracked by exportRecords.

## Mac service modes (implemented in mac_launcher.py/updater_cli.py)
All arguments originate from trusted AppKit values, never JS. Current version from Bundle Info.
- `DialogueTTSService --update-check CURRENT_VERSION`
- `DialogueTTSService --update-install APP_BUNDLE_PATH CURRENT_VERSION DISPLAYED_NEW_VERSION GUI_PID SERVICE_PID`
- `DialogueTTSService --ack-update RECEIPT_PATH NONCE`
- helper-internal `DialogueTTSService --apply-update PLAN_PATH`
Each emits one JSON result then exits. Native install flow:
1. Check JS safety; ensure no native downloads/export/dialog currently active. Hold native update gate, preventing new saves/jobs.
2. Show native explicit confirmation with version, restart and settings-preservation notice. On cancel no download.
3. Get token from local `/api/config`; POST JSON `{}` + `X-App-Token` to `/api/update/lock`. This is serialized against new job requests, rejects running job or batch, and prevents new POST. On error stop.
4. `dialogueUpdateLock(true)` then call `--update-install` in background. No system Python required. UI may say “下载并校验中”. Helper spawned ONLY after verified bytes, identity and codesign.
5. If CLI returns error, POST `/api/update/unlock`, unlock native/UI. Do not close app.
6. If waiting_for_exit, normal GUI/service shutdown (no SIGKILL for update). Helper waits up to120s for both supplied PIDs; refuses if they do not exit.
7. New native app gets `--update-receipt PATH NONCE` in process arguments. After backend AND WK page loaded, call `--ack-update`; restrict values to these OS startup args. Receipt exact private path+nonce validated in Python. No JS ack API.
8. Native existing forced-shutdown fallback must NOT be used on update. If normal service does not stop, leave existing app and report cancellation.

## State and lifetime
`~/Library/Application Support/DialogueTTS` or `%LOCALAPPDATA%/DialogueTTS`. Updater only uses `updates/<uuid>` and `installation-locks`; never reads/writes preferences.json or output directories. Installed app path supplied by native host. Mac persistent backend / Windows application holds an exclusive InstallationLease for whole life, so two v0.3 instances at same canonical path cannot run. Helper reacquires after old processes exit, then releases only after replacement, before new app launch. This guarantee applies v0.3 onward; v0.2 did not implement locking or installing.

ZIP archive and `.sha256` both need GitHub API SHA256 digest, exact name/url/size. Checksum text must agree. Strict newer stable semver. Windows checks x64 PE plus version resource ProductName/InternalName/FileVersion; Mac checks bundleID, executable, version, arm64 and codesign. Internal relative symlinks are allowed only within bundle, with no writes below symlink entries. New Mac bundle gets internet quarantine; normal `open` maintains Gatekeeper policy. No signing keys or Gatekeeper bypass.

## Recovery semantics (do not overclaim)
Copies replacement beside app on same filesystem, retains unique old backup. Windows replaces only DialogueTTS.exe, not parent folder. No active tasks killed. Native OS launch synchronous failure: reacquire install lock and roll back, reopen old via OS. Ready receipt timeout (90s) is **not failure proof**: retain new+old, write needs_attention, never kill app or overwrite possibly running new app. System prompts may require user action. Previous backups and staging retained conservatively; do not claim automatic cleanup/rollback on power loss. Transaction status in stage/status.json.

## Verification
`python -m unittest test_safe_updater` uses only temp fixtures. Native end-to-end update on Mac/Windows still required. Do not install into user's running v0.2 during build/test. First migration from v0.2 needs separate authorized bootstrap; a GitHub release cannot retrofit installer code into already-running v0.2.

### Review hardening addendum
Helper must publish nonce-matching ready before native host exits. Failed startup or deadline disarms its private transaction; helper checks the cancellation before ready, during old-process wait, and before replacement. Windows payload receives Zone.Identifier internet marking. Incoming copy uses an exclusively created private same-filesystem directory (not a predictable writable filename). Whole-download deadline is ten minutes, with socket timeouts. Unsaved result ledger survives changing voices/preview; update popover exposes still-unsaved download links. State/rollback errors are retained as status.json. Stage/backups are deliberately retained for diagnostics; do not promise silent cleanup.

### Native explicit disarm contract (required before enabling Mac install)
`--update-install ...` success now returns `{state:"waiting_for_exit",transaction_id:"32 lowercase hex",cancel_token:"64 lowercase hex"}`. Keep these two capabilities in native memory only; do not accept either from JS or display them.
If old service fails to exit normally, do NOT force terminate it and do NOT simply unlock UI. Invoke `DialogueTTSService --cancel-update TRANSACTION_ID CANCEL_TOKEN` using the same original trusted executable and retained native values. It reads only `AppSupport/DialogueTTS/updates/<ID>/plan.json`; a matching nonce is required. Result `{state:"cancelled",cancelled:true}` confirms a durable cancellation while still pre-replacement. Then native may undo its update lock and leave old app running; future ordinary exit cannot install that cancelled transaction. Result `too_late,cancelled:false` means replacement boundary already committed: do not claim cancellation or launch competing actions; keep/report pending status. CLI error/timeout means cancellation NOT confirmed; preserve old process and retry/report blocker, never kill it. Bound native CLI wait (e.g.15s) but do not treat elapsed time as successful cancellation.
Helper's final replacement boundary and cancellation share an exclusive transaction lock. Helper checks cancellation before ready, while awaiting old PIDs, and atomically before marking replacement started. No caller may unlink cancellation markers. A replacement-started transaction cannot be rearmed or cancelled.
`extract_safe` canonicalizes the newly created root with resolve before all symlink containment comparisons, allowing legitimate macOS `/var`→`/private/var` and `/tmp` aliases without weakening escape rejection.

## Source and packaging contract

Gitee is first. Only transport/service availability failures permit GitHub fallback; unsafe redirect, TLS, malformed metadata, size or digest failure remains an error. Fallback fetches the **selected version tag**, not GitHub latest, and compares archive/checksum names, lengths and hashes. The Gitee catalogue source is independent of the builtin voice catalogue and cannot establish voice/network availability.

`tools/package_macos.py` uses UTF-8 ZIP names and POSIX symlink entries without dereferencing them. The published v0.3.2 archive used a legacy filename encoding that causes existing extractors to reject symlinks. Fixes are in candidate packaging; published assets are untouched. Actual rebuilt archives must pass old v0.3.x extractors as well as current identity/signature checks. Do not relax archive containment checks.

Source exports share `tools/package_sources.py`: root tests and Python modules, workflow, layered rules, frontend sources/configuration and both ecosystems' locks are included. Dependency trees, generated builds, evidence and private inputs are excluded.

## macOS runtime and first-install compatibility

CPython on macOS does not provide the Linux `os.setxattr` API used by the old v0.3.x updater. The candidate uses `/usr/bin/xattr -w`, then reads the marker back. The actual same-filesystem incoming bundle is marked again before replacement because Python copying may drop the attribute. Failures leave the installed application in place; this never clears quarantine or changes Gatekeeper policy.

Old v0.3.0/0.3.1/0.3.2 extractors accept the new ZIP format. This does not establish full backward automatic-update compatibility with old runtime code. The owner has accepted a one-time manual installation of a future fixed release, followed by automatic updates from that fixed updater. Existing release assets are untouched. This branch is not a published fix.

`tools/macos_upgrade_smoke.py` is opt-in: it creates a private installation/profile under verification, uses the real built ZIP and frozen helper, waits for an isolated old process, retains backups and checks the native nonce receipt. It uses normal LaunchServices policy and does not clear internet-origin marking. It does not test remote release discovery or a real older GUI binary; normal current-candidate close/save is tested separately. Its evidence is private and excluded from source exports.
