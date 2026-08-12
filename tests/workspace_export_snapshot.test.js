const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8');

test('workspace face evidence export preserves review snapshots', () => {
    assert.match(appSource, /if \(evidence\?\.image_b64\) item\.image_b64 = evidence\.image_b64;/);
    assert.match(appSource, /if \(evidence\?\.thumbnail_b64\) item\.thumbnail_b64 = evidence\.thumbnail_b64;/);
});
