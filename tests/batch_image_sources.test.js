const test = require('node:test');
const assert = require('node:assert/strict');

const imageSources = require('../static/js/batch_image_sources.js');

test('faceImageSource prefers embedded face snapshot over imported archive image', () => {
    const source = imageSources.faceImageSource(
        {
            image_b64: 'face-snapshot',
            imported_image_url: 'blob:face-imported',
            thumbnail_b64: 'face-thumb',
        },
        {
            imported_image_url: 'blob:result-imported',
        },
    );

    assert.equal(source.kind, 'full');
    assert.equal(source.src, 'data:image/jpeg;base64,face-snapshot');
    assert.equal(source.fallbackSrc, 'data:image/jpeg;base64,face-thumb');
});

test('faceImageSource falls back to imported archive image when no embedded snapshot exists', () => {
    const source = imageSources.faceImageSource(
        {
            imported_image_url: 'blob:face-imported',
        },
        {
            imported_image_url: 'blob:result-imported',
        },
    );

    assert.equal(source.kind, 'imported');
    assert.equal(source.src, 'blob:face-imported');
});
