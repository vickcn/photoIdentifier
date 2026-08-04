const test = require('node:test');
const assert = require('node:assert/strict');

const relationships = require('../static/js/face_relationships.js');

test('creates one cluster relationship per photo even with duplicate evidence', () => {
    const assignments = relationships.createAssignments([
        {
            cluster_id: 'cluster_001',
            evidence_photos: [
                { file_name: 'a.jpg' },
                { file_name: 'a.jpg' },
            ],
        },
        {
            cluster_id: 'cluster_002',
            evidence_photos: [{ file_name: 'a.jpg' }, { file_name: 'b.jpg' }],
        },
    ]);

    assert.deepEqual(assignments, {
        'a.jpg': ['cluster_001', 'cluster_002'],
        'b.jpg': ['cluster_002'],
    });
});

test('buildExport expands current names and removes nested image data', () => {
    const output = relationships.buildExport({
        sessionId: 'session-1',
        batchMode: 'drive',
        exportedAt: '2026-08-01T00:00:00.000Z',
        clusters: [
            {
                cluster_id: 'cluster_001',
                display_name: '新名字',
                status: 'confirmed',
                notes: '講師',
                evidence_photos: [
                    { file_name: 'a.jpg', bbox: [1, 2, 3, 4], image_b64: 'face-secret' },
                ],
            },
        ],
        results: [
            {
                file_name: 'a.jpg',
                drive_id: 'drive-1',
                user_decision: 'safe',
                original_image_b64: 'original-secret',
                drawn_image_b64: 'drawn-secret',
                result: { moderation_status: 'public', image_b64: 'nested-secret' },
            },
        ],
        assignments: { 'a.jpg': ['cluster_001'] },
    });

    assert.equal(output.session_id, 'session-1');
    assert.equal(output.people[0].display_name, '新名字');
    assert.deepEqual(output.photos[0], {
        file_name: 'a.jpg',
        drive_id: 'drive-1',
        public_decision: 'safe',
        people: [{ cluster_id: 'cluster_001', display_name: '新名字' }],
    });
    assert.equal(JSON.stringify(output).includes('secret'), false);
    assert.deepEqual(output.face_clusters[0].evidence_photos[0].bbox, [1, 2, 3, 4]);
});

test('buildExport keeps photos without people and ignores unknown cluster ids', () => {
    const output = relationships.buildExport({
        sessionId: 'session-2',
        batchMode: 'upload',
        exportedAt: '2026-08-01T00:00:00.000Z',
        clusters: [{ cluster_id: 'cluster_001', display_name: '人物 001' }],
        results: [{ file_name: 'empty.jpg', ai_decision: 'pending' }],
        assignments: { 'empty.jpg': ['missing-cluster'] },
    });

    assert.deepEqual(output.photos[0].people, []);
    assert.equal(output.photos[0].public_decision, 'pending');
});

test('buildExport groups photos into merged person folders by display name', () => {
    const output = relationships.buildExport({
        sessionId: 'session-3',
        batchMode: 'drive',
        exportedAt: '2026-08-01T00:00:00.000Z',
        clusters: [
            { cluster_id: 'cluster_001', display_name: '王小明' },
            { cluster_id: 'cluster_002', display_name: '王小明' },
        ],
        results: [
            { file_name: 'a.jpg', drive_id: 'drive-a' },
            { file_name: 'b.jpg', drive_id: 'drive-b' },
        ],
        assignments: {
            'a.jpg': ['cluster_001'],
            'b.jpg': ['cluster_002'],
        },
    });

    assert.equal(output.people_folders.length, 1);
    assert.equal(output.people_folders[0].name, '王小明');
    assert.deepEqual(
        output.people_folders[0].photos.map(photo => [photo.file_name, photo.drive_id]),
        [['a.jpg', 'drive-a'], ['b.jpg', 'drive-b']],
    );
});
