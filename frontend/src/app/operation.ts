/** Stable operation identifiers; visible wording is never a safety protocol. */
export type OperationKind = 'import' | 'folder' | 'access' | 'settings' | 'preferences' | 'selection' | 'issue' | 'start' | 'resume' | 'pause' | 'cancel' | 'retry_save' | 'retry_synthesis' | 'finish' | 'audition' | 'reveal' | 'update_check' | 'update_install' | 'report';
export interface Operation {kind: OperationKind; label: string}
export function blocksUpdate(operation: Operation | null) {
  return !!operation && operation.kind !== 'update_check' && operation.kind !== 'update_install';
}
