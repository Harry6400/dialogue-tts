export type Role = 'AGENT' | 'USER';
export type VoiceId = 'zh-CN-XiaoxiaoNeural' | 'zh-CN-XiaoyiNeural' | 'zh-CN-YunxiNeural' | 'zh-CN-YunjianNeural' | 'zh-CN-YunxiaNeural' | 'zh-CN-YunyangNeural';
export interface Voice {id: VoiceId; label: string; locale: 'zh-CN'; gender: 'Female' | 'Male'}
export interface XmlFile {name: string; content: string}
export interface Config {token: string; voices: Voice[]; catalog_revision: string; catalog_source: string; xml_example: XmlFile[]; can_shutdown: boolean}
export interface Line {id: number; speaker: Role; text: string; voice: VoiceId; pause_after_ms: number; speech_rate: number}
export interface PreviewRow {id: number; speaker: Role; text: string; start_ms?: number; end_ms?: number; muted?: boolean; overlap?: boolean}
export interface Script {lesson_id: string; title: string; language: string; lines: Line[]; settings: {speech_rate: number; initial_silence_ms: number; default_pause_ms: number}}
export interface SoundSettings {voices: Partial<Record<Role, VoiceId>>; rate: number; pause_ms: number; auto_voice: boolean; manual_user_voice: boolean; catalog_revision?: string}
export interface PairPayload extends SoundSettings {format: 'xml_pair'; xml_files: XmlFile[]; xml_time_unit?: 'ms'}
export interface BatchPayload extends SoundSettings {files: XmlFile[]; pairs?: never[]; excluded?: string[]; voice_overrides?: Record<string, VoiceId>; xml_time_unit?: 'ms'}
export interface Preview {script: Script; rows: PreviewRow[]; speakers: Role[]; warnings: string[]; fingerprint: string; muted_count: number; sources: {name: string; role: Role; segments: number}[]; voice_choice: {user: VoiceId; reason: string; address: 'female' | 'male' | 'undetermined'}}
export interface BatchGroup {id: string; name: string; file_indices: number[]; status: 'ready' | 'error'; error?: string; error_code?: string; fingerprint?: string; preview?: Preview}
export interface BatchPreview {groups: BatchGroup[]; counts: {ready: number; error: number}}
export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled';
export interface JobSnapshot {id: string; status: JobStatus; completed: number; total: number; error?: string; download_url?: string; fingerprint?: string; duration_ms?: number}
export interface BatchItem extends Omit<JobSnapshot, 'status'> {name: string; status: JobStatus | 'pending'; filename?: string}
export interface BatchSnapshot {id: string; status: JobStatus | 'partial'; completed: number; total: number; items: BatchItem[]; zip_url?: string}
export interface Folder {folder_id: string; path: string; valid?: boolean}
export interface Preferences {ok: boolean; input_folder?: Folder | null; output_folder?: Folder | null; voices?: Partial<Record<Role, string>>; rate?: number; pause_ms?: number; theme?: 'light' | 'dark'; auto_voice?: boolean; manual_user_voice?: boolean}
export interface SaveReceipt {ok: true; save_id: string; name: string; path: string; resource_path?: string; fingerprint?: string; warning?: string}
export interface NativeFailure {ok?: false; cancelled?: boolean; error?: string}
export type UpdateResult = {state: 'latest' | 'available'; current_version: string; latest_version: string; source?: 'gitee' | 'github'} | {state: 'waiting_for_exit' | 'cancelled' | 'error'; message?: string};
export interface Safety {busy: boolean; unsaved: boolean}
export interface PreferenceWrite extends SoundSettings {theme: 'light' | 'dark'}
