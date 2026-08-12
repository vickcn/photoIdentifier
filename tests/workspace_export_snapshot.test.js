const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8');

test('workspace face evidence export preserves review snapshots', () => {
    assert.match(appSource, /if \(evidence\?\.image_b64\) item\.image_b64 = evidence\.image_b64;/);
    assert.match(appSource, /if \(evidence\?\.thumbnail_b64\) item\.thumbnail_b64 = evidence\.thumbnail_b64;/);
});

test('legacy imported face evidence can infer the 800px preview bbox basis', () => {
    assert.match(appSource, /LEGACY_FACE_BBOX_PREVIEW_MAX_SIZE = 800/);
    assert.match(appSource, /function inferLegacyPreviewBboxBasis/);
    assert.match(appSource, /\['imported', 'drive', 'local_path'\]\.includes\(sourceKind\)/);
    assert.match(appSource, /bbox_basis_width: Math\.round\(targetWidth \* scale\)/);
    assert.match(appSource, /bbox_basis_height: Math\.round\(targetHeight \* scale\)/);
});
