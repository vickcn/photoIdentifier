(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.UploadLimits = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    function splitLocalFilesByUploadLimit(files, limits) {
        const maxFileBytes = Number(limits?.maxFileBytes || 0);
        const accepted = [];
        const rejected = [];

        Array.from(files || []).forEach(file => {
            if (maxFileBytes > 0 && Number(file.size || 0) > maxFileBytes) {
                rejected.push({
                    file,
                    file_name: file.name || '未命名檔案',
                    size: Number(file.size || 0),
                    limit: maxFileBytes,
                    reason: 'file_too_large',
                });
            } else {
                accepted.push(file);
            }
        });

        return { accepted, rejected };
    }

    return { splitLocalFilesByUploadLimit };
});
