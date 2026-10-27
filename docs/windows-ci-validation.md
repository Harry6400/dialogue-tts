# Windows update validation

The CI helper never presses **Run**, suppresses a security prompt, clears Mark of the Web, changes system policy, or treats a running process as a ready application.

`windows-upgrade-smoke.json` separates outcomes:

- `outcome: verified`, `passed: true`, `end_to_end_passed: true`: actual temporary installation replaced, native window loaded and acknowledged the nonce-bound receipt; old backup and preferences/cache verified intact.
- `outcome: requires_system_confirmation`, `passed: false`, `end_to_end_passed: false`, `ci_gate_passed: true`: a narrowly verified OS **Open File - Security Warning** window is holding the normal shell launch. The helper reached its bounded `needs_attention` outcome, with correct target hash, old processes exited, all integrity/wait/cancellation/preservation cases passing. This permits packaging, but is **not** an end-to-end update pass.
- Other missing readiness, unknown dialogs, a shell call that returned without readiness, bad hashes or preservation failures fail CI. The archive step runs only after this build gate returns successfully.

Evidence for the v0.3 updater's complete Windows transaction was collected separately with specific owner approval for one OS confirmation:

- Commit `7071f8adf96f6f9a2d07fc1ca438b23a632d246d`
- Run https://github.com/Harry6400/dialogue-tts/actions/runs/40472239465
- Artifact `12719730547`
- Unpublished test candidate `0.3.1`, SHA256 `b779b54534840e6ecc8bd2b06cd38108cdc6a2bfc33c80272905022c32baaf06`
- `transaction_state=complete`, `receipt_state=ready`, all six transaction cases passed. Native events recorded `window_loaded` then `ack_result=true`.

The test staged a locally built candidate; it did **not** prove an actual GitHub release download or install anything on the user's computer. Folder-dialog interactions were tested separately. The test exposed a Windows short-path/long-path receipt mismatch; canonicalizing both receipt and state root fixed it, without weakening nonce or private-directory checks.

The temporary confirmation mechanism used for that exact test has been removed from the workflow and harness. No continuing confirmation permission is stored. Subsequent unattended runs retain Windows security prompts. The cleanup changes test/reporting code only; production updater and application logic are unchanged from the successful runtime source.
