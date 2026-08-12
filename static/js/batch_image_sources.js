(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.BatchImageSources = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    function localFileSrc(path) {
        return path ? `/local_file/?path=${encodeURIComponent(path)}` : null;
    }

    function driveFileSrc(fileId) {
        return fileId ? `/drive_file/${encodeURIComponent(fileId)}` : null;
    }

    function originalImageSrc(item) {
        if (item?.original_image_b64) {
            return `data:image/jpeg;base64,${item.original_image_b64}`;
        }
        return localFileSrc(item?.original_path) || driveFileSrc(item?.drive_id);
    }

    function annotatedImageSrc(item) {
        if (item?.drawn_image_b64) {
            return `data:image/jpeg;base64,${item.drawn_image_b64}`;
        }
        if (item?.output_b64) {
            return `data:image/jpeg;base64,${item.output_b64}`;
        }
        return localFileSrc(item?.output);
    }

    function faceImageSource(face, matchedResult) {
        const importedSrc = face?.imported_image_url || matchedResult?.imported_image_url || null;
        const thumbnailSrc = face?.thumbnail_b64 ? `data:image/jpeg;base64,${face.thumbnail_b64}` : null;
        if (face?.image_b64) {
            return { kind: 'full', src: `data:image/jpeg;base64,${face.image_b64}`, fallbackSrc: thumbnailSrc };
        }
        if (matchedResult) {
            const src = originalImageSrc(matchedResult);
            if (src) return { kind: 'result', src, fallbackSrc: thumbnailSrc };
        }
        if (importedSrc) {
            return { kind: 'imported', src: importedSrc, fallbackSrc: thumbnailSrc };
        }
        if (face?.source_type === 'drive' && face?.source_key) {
            return { kind: 'drive', src: driveFileSrc(face.source_key), fallbackSrc: thumbnailSrc };
        }
        if (face?.source_type === 'local_path' && face?.source_key) {
            return { kind: 'local_path', src: localFileSrc(face.source_key), fallbackSrc: thumbnailSrc };
        }
        if (thumbnailSrc) {
            return { kind: 'thumbnail', src: thumbnailSrc, fallbackSrc: null };
        }
        return { kind: 'placeholder', src: null, fallbackSrc: null };
    }

    function faceImageFallbackMessage(source) {
        if (source === 'drive') return '雲端模式可從 Google 來源重新載入完整圖。';
        if (source === 'local_path') return '本機模式會嘗試讀取目前 session 的本機路徑。';
        if (source === 'thumbnail') return '完整圖暫時不可用，先顯示持久化縮圖。';
        return '本機模式的完整圖只保證在目前 session 存活期內可用。';
    }

    return {
        driveFileSrc,
        faceImageFallbackMessage,
        faceImageSource,
        localFileSrc,
        originalImageSrc,
        annotatedImageSrc,
    };
});
