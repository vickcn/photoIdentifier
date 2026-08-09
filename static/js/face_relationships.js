(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.PhotoRelationships = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
    const IMAGE_KEYS = new Set(['image_b64', 'original_image_b64', 'drawn_image_b64']);

    function stripImages(value) {
        if (Array.isArray(value)) return value.map(stripImages);
        if (!value || typeof value !== 'object') return value;
        return Object.fromEntries(
            Object.entries(value)
                .filter(([key]) => !IMAGE_KEYS.has(key))
                .map(([key, item]) => [key, stripImages(item)]),
        );
    }

    function createAssignments(clusters) {
        const assignments = {};
        (clusters || []).forEach(cluster => {
            const clusterId = String(cluster.cluster_id || '');
            if (!clusterId) return;
            (cluster.evidence_photos || []).forEach(evidence => {
                const fileName = String(evidence.file_name || '');
                if (!fileName) return;
                if (!assignments[fileName]) assignments[fileName] = [];
                if (!assignments[fileName].includes(clusterId)) assignments[fileName].push(clusterId);
            });
        });
        return assignments;
    }

    function normalizeFolderName(value, fallback) {
        return String(value || fallback || '未命名人物')
            .trim()
            .replace(/[\\/:*?"<>|]/g, '_')
            .replace(/\s+/g, ' ')
            .slice(0, 80) || '未命名人物';
    }

    function faceCountFolderName(faceCount) {
        const count = Number.isFinite(faceCount) ? Math.max(0, Math.trunc(faceCount)) : 0;
        return `${count}人`;
    }

    function readPublicDecision(item) {
        const analysis = item.result || item;
        const explicit = item.user_decision || item.ai_decision || analysis.ai_decision;
        if (explicit) return explicit;
        if (analysis.moderation_status === 'public' || analysis.is_safe_for_public === true) return 'safe';
        if (analysis.moderation_status === 'pending') return 'pending';
        return 'unsafe';
    }

    function buildExport({ sessionId, batchMode, clusters, results, assignments, exportedAt }) {
        const cleanClusters = stripImages(clusters || []);
        const peopleById = new Map(
            cleanClusters.map(cluster => [String(cluster.cluster_id), cluster]),
        );
        const people = cleanClusters.map(cluster => ({
            cluster_id: String(cluster.cluster_id),
            display_name: String(cluster.display_name || cluster.cluster_id),
            status: cluster.status || 'unconfirmed',
            notes: cluster.notes || '',
        }));
        const photos = (results || []).map(item => {
            const analysis = item.result || item;
            const fileName = String(item.file_name || item.file || analysis.file_name || analysis.file || '');
            const assignedIds = Array.from(assignments?.[fileName] || []);
            const assignedPeople = assignedIds
                .map(clusterId => peopleById.get(String(clusterId)))
                .filter(Boolean)
                .map(cluster => ({
                    cluster_id: String(cluster.cluster_id),
                    display_name: String(cluster.display_name || cluster.cluster_id),
                }));
            const photo = {
                file_name: fileName,
                public_decision: readPublicDecision(item),
                people: assignedPeople,
            };
            const driveId = item.drive_id || analysis.drive_id;
            if (driveId) photo.drive_id = driveId;
            return photo;
        });
        const foldersByName = new Map();
        photos.forEach(photo => {
            const people = photo.people || [];
            const countFolderName = faceCountFolderName(people.length);
            const pathSegments = people.length === 1
                ? [countFolderName, normalizeFolderName(people[0].display_name, people[0].cluster_id)]
                : [countFolderName];
            const folderKey = pathSegments.join('/');
            const displayName = pathSegments[pathSegments.length - 1];
            if (!foldersByName.has(folderKey)) {
                foldersByName.set(folderKey, {
                    name: displayName,
                    face_count: people.length,
                    path_segments: pathSegments,
                    photos: [],
                });
            }
            foldersByName.get(folderKey).photos.push({
                file_name: photo.file_name,
                drive_id: photo.drive_id || null,
                people: people.map(item => ({
                    cluster_id: item.cluster_id,
                    display_name: item.display_name,
                })),
            });
        });

        const photoAngleFolders = Array.from(foldersByName.values());

        return {
            exported_at: exportedAt,
            session_id: sessionId,
            batch_mode: batchMode,
            image_count: photos.length,
            people,
            photo_angle_folders: photoAngleFolders,
            photos,
            face_clusters: cleanClusters,
            results: stripImages(results || []),
        };
    }

    return { createAssignments, buildExport, stripImages };
});
