# Photo Person Relationships Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add editable person and photo perspectives, maintain photo-to-person relationships, export the relationship JSON locally, and additionally save a timestamped JSON file to a selected Google Drive output folder.

**Architecture:** Keep `cluster_id` as the canonical person identifier and store photo assignments as filename-to-cluster-ID sets in frontend session state. Put deterministic relationship initialization, sanitization, and export shaping in a small browser/Node-compatible module; keep DOM rendering in the existing app. Add one authenticated FastAPI resource endpoint that validates batch ownership and writes the exact frontend-generated JSON document to Drive.

**Tech Stack:** FastAPI, Pydantic, Google Drive API, vanilla JavaScript, CSS, Python unittest/pytest, Node built-in test runner.

## Global Constraints

- Use Traditional Chinese for UI copy and preserve UTF-8 encoding.
- Apply minimal changes; do not restructure unrelated application code.
- Person names live on face clusters; photo relationships store only stable `cluster_id` values.
- Both local and Drive modes download JSON to the current device.
- Drive mode with a target folder additionally creates `photo_people_YYYYMMDD_HHMMSS.json` and never overwrites an older export.
- Remove all image base64 fields from relationship JSON.
- Reject Drive JSON documents larger than 10 MB.
- A Drive backup failure must not undo or block the local browser download.

---

### Task 1: Authenticated Drive JSON Export API

**Files:**
- Modify: `main.py` near the face-cluster resource endpoints
- Modify: `tests/test_face_workspace_api.py`

**Interfaces:**
- Consumes: `_owned_batch_session(request, session_id)` and `get_drive_credentials(request)`.
- Produces: `POST /batch_exports/drive` accepting `DriveBatchExportRequest(session_id, target_folder_id, document)` and returning `status`, `file_id`, `file_name`.

- [ ] **Step 1: Write failing ownership and success tests**

Add tests that create a drive-owned batch session, patch `_save_json_export_to_drive`, and assert both authorization and the timestamp filename contract:

```python
def test_drive_export_requires_owned_batch_session(self):
    with patch.object(main, "_get_client_id", return_value="owner-b"):
        response = self.client.post("/batch_exports/drive", json={
            "session_id": "face-workspace-test",
            "target_folder_id": "target-1",
            "document": {"session_id": "face-workspace-test"},
        })
    self.assertEqual(response.status_code, 404)

def test_drive_export_creates_timestamped_json(self):
    main._batch_sessions["face-workspace-test"]["batch_mode"] = "drive"
    document = {"session_id": "face-workspace-test", "photos": []}
    with (
        patch.object(main, "_get_client_id", return_value="owner-a"),
        patch.object(main, "get_drive_credentials", return_value=object()),
        patch.object(main, "_save_json_export_to_drive", return_value={"id": "file-1"}) as save,
    ):
        response = self.client.post("/batch_exports/drive", json={
            "session_id": "face-workspace-test",
            "target_folder_id": "target-1",
            "document": document,
        })
    self.assertEqual(response.status_code, 200)
    self.assertRegex(response.json()["file_name"], r"^photo_people_\d{8}_\d{6}\.json$")
    self.assertEqual(save.call_args.args[1], "target-1")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `source ~/tchop/bin/activate && python -m pytest tests/test_face_workspace_api.py -q`

Expected: FAIL because `/batch_exports/drive` and `_save_json_export_to_drive` do not exist.

- [ ] **Step 3: Add validation tests**

Add separate tests asserting 400 for mismatched document `session_id`, 400 for non-Drive sessions, 413 for a document whose encoded JSON exceeds 10 MB, and 401 when credentials are absent.

- [ ] **Step 4: Implement the minimal endpoint and Drive writer**

Add:

```python
class DriveBatchExportRequest(BaseModel):
    session_id: str
    target_folder_id: str
    document: dict[str, Any]

def _save_json_export_to_drive(credentials, target_folder_id: str, file_name: str, content: bytes) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    import io

    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json", resumable=False)
    return service.files().create(
        body={"name": file_name, "mimeType": "application/json", "parents": [target_folder_id]},
        media_body=media,
        fields="id,name",
    ).execute()
```

The endpoint must trim IDs, validate owned drive session and matching document session, encode with `ensure_ascii=False`, enforce `10 * 1024 * 1024`, call the writer through `run_in_threadpool`, and map credential errors to 401 and Drive errors to 500.

- [ ] **Step 5: Run backend tests and verify GREEN**

Run: `source ~/tchop/bin/activate && python -m pytest tests/test_face_workspace_api.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the API task**

```bash
git add main.py tests/test_face_workspace_api.py
git commit -m "Add Drive relationship JSON export API"
```

### Task 2: Tested Relationship Data Module

**Files:**
- Create: `static/face_relationships.js`
- Create: `tests/face_relationships.test.js`
- Modify: `template/index.html` before the `app.js` script

**Interfaces:**
- Produces: global/CommonJS `PhotoRelationships` with `createAssignments(clusters)`, `buildExport({sessionId, batchMode, clusters, results, assignments, exportedAt})`, and `stripImages(value)`.
- Consumed by: Task 3 UI state and Task 4 download flow.

- [ ] **Step 1: Write failing Node tests**

Test automatic deduplication, renamed display-name expansion, empty assignments, nested image stripping, and stable photo records:

```javascript
test('creates one cluster relationship per photo even with duplicate evidence', () => {
  const assignments = relationships.createAssignments([{cluster_id: 'cluster_001', evidence_photos: [
    {file_name: 'a.jpg'}, {file_name: 'a.jpg'}
  ]}]);
  assert.deepEqual(assignments['a.jpg'], ['cluster_001']);
});

test('buildExport expands current person names and strips image base64', () => {
  const output = relationships.buildExport({
    sessionId: 's1', batchMode: 'upload', exportedAt: '2026-08-01T00:00:00.000Z',
    clusters: [{cluster_id: 'cluster_001', display_name: '新名字', evidence_photos: [{file_name: 'a.jpg', image_b64: 'secret'}]}],
    results: [{file_name: 'a.jpg', user_decision: 'safe', original_image_b64: 'secret'}],
    assignments: {'a.jpg': ['cluster_001']},
  });
  assert.equal(output.photos[0].people[0].display_name, '新名字');
  assert.equal(JSON.stringify(output).includes('secret'), false);
});
```

- [ ] **Step 2: Run Node tests and verify RED**

Run: `node --test tests/face_relationships.test.js`

Expected: FAIL because `static/face_relationships.js` does not exist.

- [ ] **Step 3: Implement the minimal pure module**

Use an IIFE that assigns both `module.exports` and `window.PhotoRelationships`. `createAssignments` returns plain arrays for serialization; `buildExport` returns `people`, `photos`, sanitized `face_clusters`, and sanitized `results`. Each photo record includes `file_name`, optional `drive_id`, `public_decision`, and people expanded from IDs while ignoring unknown IDs.

- [ ] **Step 4: Load the module before app.js**

Add a cache-busted script immediately before `app.js`:

```html
<script src="/static/face_relationships.js?v=20260801-photo-people"></script>
```

- [ ] **Step 5: Run Node tests and verify GREEN**

Run: `node --test tests/face_relationships.test.js && node --check static/face_relationships.js`

Expected: all tests pass.

- [ ] **Step 6: Commit the data module task**

```bash
git add static/face_relationships.js tests/face_relationships.test.js template/index.html
git commit -m "Add photo person relationship export model"
```

### Task 3: Person and Photo Perspective UI

**Files:**
- Modify: `static/app.js` batch overview state and render functions
- Modify: `static/app.css` face workspace and modal styles
- Modify: `template/index.html` add one shared relationship modal

**Interfaces:**
- Consumes: `PhotoRelationships.createAssignments(currentFaceClusters)`.
- Produces: `relationshipViewMode`, `photoPeopleAssignments`, `photoClusterUi.expanded`, and modal save/close actions used by Task 4 export.

- [ ] **Step 1: Add UI state initialized exactly once per completed batch**

Add:

```javascript
let relationshipViewMode = 'people';
let photoPeopleAssignments = {};
const photoClusterUi = { expanded: new Set() };

function initializePhotoPeopleAssignments() {
  photoPeopleAssignments = PhotoRelationships.createAssignments(currentFaceClusters);
  currentBatchResults.forEach(item => {
    const fileName = item.file_name || item.file;
    if (!(fileName in photoPeopleAssignments)) photoPeopleAssignments[fileName] = [];
  });
}
```

Call it only after completed `face_clusters` are assigned, and clear it when a new batch starts.

- [ ] **Step 2: Add the perspective switch and person inline name editing**

Render `人物角度` and `照片角度` buttons inside the face workspace toolbar. Keep the existing person accordion; add an always-visible compact name input and save button on each person row, while the expanded editor continues to own status and notes. Both save paths call the existing cluster PATCH function and re-render names in both perspectives.

- [ ] **Step 3: Render the collapsed photo perspective**

Each photo row shows filename, final public decision, and relationship count. Its disclosure button uses a separate `data-photo-action="toggle-photo"`; expanded content shows the thumbnail, public-decision badge, current person chips, `編輯人物`, and `查看完整照片`.

- [ ] **Step 4: Add one shared relationship modal**

Add `#photo-people-modal` to the template with title, filename, checkbox list, cancel, and save controls. Opening fills checkboxes from `currentFaceClusters` and preselects IDs from `photoPeopleAssignments[fileName]`. Saving replaces only that file's array, closes the modal, and re-renders the photo perspective. Escape and overlay clicks close without saving.

- [ ] **Step 5: Add responsive, focus, selected, and empty states**

Use the existing paper/warm design variables. Preserve three-column face evidence; stack photo details on widths below 820px. Add `:focus-visible`, explicit button cursors, selected checkbox-row styling, and `未偵測到人物` modal copy.

- [ ] **Step 6: Run syntax and frontend validator checks**

Run:

```bash
node --check static/app.js
source ~/tchop/bin/activate && python /Users/kexuen/projects/skills/frontend-micro-interactions/scripts/validate_tool_surface.py static/app.js template/index.html static/app.css
```

Expected: syntax succeeds and validator reports zero FAIL/WARN applicable to this page.

- [ ] **Step 7: Commit the UI task**

```bash
git add static/app.js static/app.css template/index.html
git commit -m "Add person and photo relationship views"
```

### Task 4: Local Download and Optional Drive Backup

**Files:**
- Modify: `static/app.js` export and download flow
- Modify: `tests/face_relationships.test.js` for final document shape
- Modify: `template/index.html` static asset version strings

**Interfaces:**
- Consumes: `PhotoRelationships.buildExport(...)`, `driveTargetId`, `batchMode`, and `POST /batch_exports/drive`.
- Produces: one identical JSON document downloaded locally and optionally stored on Drive.

- [ ] **Step 1: Extend the export test for the final contract**

Assert that every photo has `file_name`, `public_decision`, and `people`; every expanded person has `cluster_id` and current `display_name`; and no image base64 key survives anywhere in the output.

- [ ] **Step 2: Run the focused test and verify RED if contract fields are missing**

Run: `node --test tests/face_relationships.test.js`

Expected: FAIL on any missing final-contract field.

- [ ] **Step 3: Replace buildBatchResultExport with the shared builder**

Call:

```javascript
PhotoRelationships.buildExport({
  sessionId: window._currentSessionId || null,
  batchMode,
  clusters: currentFaceClusters,
  results: currentBatchResults,
  assignments: photoPeopleAssignments,
  exportedAt: new Date().toISOString(),
});
```

- [ ] **Step 4: Add non-blocking Drive backup after local download**

Create `saveExportToDrive(document)` that returns immediately unless mode is `drive` and `driveTargetId.value.trim()` is non-empty. POST the exact document to `/batch_exports/drive`; show `本機已下載，並已備份至雲端輸出區` on success and `本機已下載，但雲端備份失敗：...` on failure. Do not retry automatically.

- [ ] **Step 5: Ensure every local-download branch invokes Drive backup**

Refactor the JSON-only, oversized ZIP fallback, ZIP success, and ZIP-error branches so the local download happens first and `await saveExportToDrive(exportData)` happens once in a common completion path. Preserve the existing annotated ZIP behavior.

- [ ] **Step 6: Update asset versions and run complete verification**

Run:

```bash
node --test tests/face_relationships.test.js
node --check static/app.js
node --check static/face_relationships.js
source ~/tchop/bin/activate && python -m pytest tests -q
source ~/tchop/bin/activate && python /Users/kexuen/projects/skills/frontend-micro-interactions/scripts/validate_tool_surface.py static/app.js template/index.html static/app.css
git diff --check
```

Expected: all Node and Python tests pass, syntax checks pass, validator has zero FAIL/WARN, and diff check is clean.

- [ ] **Step 7: Manually verify the two source modes**

Verify these four cases in the local browser without exposing secrets in Console:

```text
local/upload + download -> local JSON/ZIP only
drive + no target -> local JSON/ZIP only
drive + target -> local JSON/ZIP plus timestamped Drive JSON
Drive API failure -> local file remains downloaded and error is visible
```

- [ ] **Step 8: Commit the completed integration**

```bash
git add static/app.js static/face_relationships.js tests/face_relationships.test.js template/index.html
git commit -m "Export photo person relationships locally and to Drive"
```
