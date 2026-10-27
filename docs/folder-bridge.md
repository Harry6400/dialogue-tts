# Folder bridge (v0.3.4)

The frontend has no filesystem authority. Paths are display text only. Native hosts issue opaque `folder_id` capabilities after the user chooses a directory, or restores the persisted native authorization. Invalid restored permissions must set `valid: false`. Restoring preferences never scans XML or submits synthesis.

Windows exposes Promise-returning `window.pywebview.api` methods below. macOS uses `dialogueFolders.postMessage({request_id, method, args})`, resolving through `window.dialogueNativeFolderResponse({request_id, result})`. Unknown request IDs are ignored. Preserve existing `dialogueNativeSaved`, `reveal_saved`, and native directory-selection handling for manual MP3 saves.

| Method | Arguments | Result |
|---|---|---|
| `request_close` | none | `{ok}` requests the same guarded exit as native close/menu |
| `check_output_space` | `folder_id` | `{ok,writable,free_bytes}`; a path string is not authorization |
| `get_preferences` | none | `{ok, input_folder, output_folder, voices, rate, pause_ms}`; folder values are null or `{folder_id,path,valid}` |
| `set_preferences` | `{voices, rate, pause_ms}` | `{ok}`; only the six builtin zh-CN voice IDs, finite rate 0.5–2, integer pause_ms 0–3000 |
| `choose_input_directory` | none | `{ok,folder_id,path,files:[{name,content}]}` |
| `refresh_input_directory` | `folder_id` | same as input selection, on explicit click only |
| `choose_output_directory` | none | `{ok,folder_id,path}` |
| `check_completed` | `folder_id, fingerprints, expected_names?` | `{ok,completed:[{fingerprint,name,path,save_id}],conflicts:[{fingerprint,name,error}]}` |
| `save_download` | `resource_path` | directory-only manual MP3 save, fixed trusted response filename; does not change batch default folder |
| `save_to_output` | `resource_path, folder_id` | `{ok,save_id,name,path,fingerprint}` only after durable save |
| `reveal_output` | `folder_id` | `{ok}` |

Cancellation returns `{cancelled:true}` without changing the current UI selection. Failure returns `{ok:false,error}`. The UI sends `args` as an object for macOS, and positional arguments as listed for Windows (set_preferences receives its object).

Input enumeration is bounded to 100 regular XML files, current directory only, 2 MiB per file, 20 MiB total UTF-8 XML; no symbolic links, recursion or arbitrary JS paths. Backend independently validates these bounds, 50 calls, and a 64 MiB JSON request envelope. Strict grouping uses exact string `callInfo/callReference` and one AGENT plus one USER. Native must not infer pairs.

Native output accepts only whitelisted local audio routes, validates the response `X-Dialogue-Fingerprint`, and records successful output with its actual byte count and SHA-256. `check_completed` must verify the selected directory's existing actual MP3 against that record, not only filename or fingerprint. Missing/modified files are not completed. No overwrite or numbered suffix: use only the validated numeric `Content-Disposition` filename returned by the local backend. Publish a flushed temporary file atomically without replacing an existing entry; racing files and symlinks are conflicts. On Windows use no-replace rename; on macOS use an exclusive hard link. Keep temporary generated audio in the job on save failure. The frontend's remembered completion map is not authoritative.

The frontend serializes exports and captures folder capability per job. Native also makes `(folder_id, resource_path)` idempotent, including a repeated request after a lost callback. The optional `expected_names` maps each requested fingerprint to its exact numeric filename. With this mapping, a legacy generic-name receipt cannot complete a new numeric target. The same fingerprint and expected name is skipped only after receipt, byte count and actual SHA-256 match; otherwise any existing expected name is a conflict. A changed resource route or regeneration cannot bypass this check. Old generic outputs are not renamed. Failed save retries reuse the generated audio rather than starting TTS. Native persists only legitimate successful records; frontend never supplies an output fingerprint to the save method.

Controls remain locked while parsing, synthesis or export is in progress. Manual single-file save remains available as a fallback. Paths, voices and output records must stay on the user's machine and never be committed to the public repository.

Persistent native preferences/cache belong in platform app-data, outside application bundles and release folders so replacing/updating the app preserves them. Restoring valid rate and pause only updates controls; no preview scan or TTS is triggered.

## Normal exit and save ledger

`window.dialogueCloseSafety()` returns boolean `busy,running,saving,pending,unsaved`. A missing or malformed result is unknown and blocks ordinary exit. `window.dialogueShowUnsaved()` exposes real generated-audio links. Native save/export receipts identify the actual resource route before clearing the unsaved ledger. Batch saves use the job's captured native capability, never the current mutable folder selection. Unsupported/failed/cancelled native operations cannot represent successful saves.

macOS verification candidates may receive native process arguments `--verification-state ABSOLUTE_DIRECTORY`, using a separate preferences file. This is not a webpage method and grants no JS filesystem path authority. Normal launches continue to use platform app-data. For complete verification transactions, an explicit native OS environment `DIALOGUE_VERIFICATION_STATE_ROOT` selects the same private profile for preferences, helper plans and receipts; Python requires an absolute, non-symlink, current-user-owned directory with private permissions. The helper forwards only this explicit verification profile through LaunchServices. Normal launches do not set it.

## Public naming contract

Pair XML by exact callReference and AGENT/USER first. Each pair must have the same nonempty ASCII digit prefix, suffixes 0 and 1, and a case-insensitive .xml extension. Output is that prefix plus .mp3; preserve leading zeros and long numbers as strings. Preview, job snapshots, batch items, Content-Disposition and ZIP members share the server-owned filename. Invalid groups and every duplicate target in a batch are errors and are skipped while valid groups continue. Filename belongs to request-id binding, not the audio fingerprint.

Mac page-initiated request_close schedules NSApp termination through the main RunLoop, outside a main-dispatch block. terminateLater needs an asynchronous main-thread reply, and nesting its wait inside an occupied main-queue block can starve that reply. Task, saving and unsaved-result checks still use the same delegate. See [Apple RunLoop scheduling](https://developer.apple.com/documentation/foundation/runloop/perform(inmodes:block:)) and [termination replies](https://developer.apple.com/documentation/appkit/nsapplication/reply(toapplicationshouldterminate:)).
