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

    function originalImageSrc(item) {
        if (item?.original_image_b64) {
            return `data:image/jpeg;base64,${item.original_image_b64}`;
        }
        return localFileSrc(item?.original_path);
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

    return {
        localFileSrc,
        originalImageSrc,
        annotatedImageSrc,
    };
});
