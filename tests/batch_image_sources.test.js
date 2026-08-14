const test = require('node:test');
const assert = require('node:assert/strict');

const imageSources = require('../static/js/batch_image_sources.js');

test('preview URLs take precedence over embedded base64 and legacy fallbacks', () => {
    const item = {
        original_preview_url: 'https://storage.example/original.jpg',
        annotated_preview_url: 'https://storage.example/annotated.jpg',
        original_image_b64: 'legacy-original',
        drawn_image_b64: 'legacy-annotated',
        drive_id: 'drive-file-id',
    };

    assert.equal(imageSources.originalImageSrc(item), item.original_preview_url);
    assert.equal(imageSources.annotatedImageSrc(item), item.annotated_preview_url);
});

test('legacy base64 and drive sources remain available when preview URLs are absent', () => {
    assert.equal(
        imageSources.originalImageSrc({ original_image_b64: 'legacy-original' }),
        'data:image/jpeg;base64,legacy-original',
    );
    assert.equal(
        imageSources.originalImageSrc({ drive_id: 'drive-file-id' }),
        '/drive_file/drive-file-id',
    );
});

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
