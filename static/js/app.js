document.addEventListener('DOMContentLoaded', () => {
    // === DOM Elements ===
    const tabBtns = document.querySelectorAll('.tab-btn');
    const modeContents = document.querySelectorAll('.mode-content');

    // Single Mode Elements
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const analyzeSingleBtn = document.getElementById('analyze-single-btn');
    let singleSelectedFile = null;

    // Batch Mode Elements
    const batchSourceRadios = document.querySelectorAll('input[name="batch-source"]');
    const localBatchInputs = document.getElementById('local-batch-inputs');
    const driveBatchInputs = document.getElementById('drive-batch-inputs');
    const googleLoginBtn = document.getElementById('google-login-btn');

    const batchDropZone = document.getElementById('batch-drop-zone');
    const batchFileInput = document.getElementById('batch-file-input');
    const batchFileSummary = document.getElementById('batch-file-summary');
    const batchCloudGuidance = document.getElementById('batch-cloud-guidance');
    const batchConcurrency = document.getElementById('batch-concurrency');
    const batchConcurrencyHint = document.getElementById('batch-concurrency-hint');
    const faceClusterEpsInput = document.getElementById('face-cluster-eps');
    const faceClusterMinSamplesInput = document.getElementById('face-cluster-min-samples');
    const batchRunPublic = document.getElementById('batch-run-public');
    const batchRunFaces = document.getElementById('batch-run-faces');
    const processingScopeHint = document.getElementById('processing-scope-hint');
    const analyzeBatchBtn = document.getElementById('analyze-batch-btn');
    const organizeArea = document.getElementById('organize-area');
    const safeFolder = document.getElementById('safe-folder');
    const unsafeFolder = document.getElementById('unsafe-folder');
    const organizeBtn = document.getElementById('organize-btn');

    // Drive Elements
    const driveFolderId = document.getElementById('drive-folder-id');
    const driveTargetId = document.getElementById('drive-target-id');

    function getSelectedBatchSource() {
        return document.querySelector('input[name="batch-source"]:checked')?.value || 'local';
    }

    function getBatchConcurrencyCap(source = getSelectedBatchSource()) {
        if (source === 'drive') {
            return config?.batch_upload_concurrency_cloud_cap || 3;
        }
        return config?.batch_upload_concurrency_local_cap || config?.batch_upload_concurrency || 5;
    }

    function getBatchConcurrencyHint(source = getSelectedBatchSource()) {
        if (source === 'drive') {
            return config?.batch_upload_concurrency_cloud_message || `Google 雲端一次最多先看 ${getBatchConcurrencyCap(source)} 張，這樣整理起來會比較穩。`;
        }
        return config?.batch_upload_concurrency_local_message || `這台電腦一次最多先看 ${getBatchConcurrencyCap(source)} 張，我會慢慢幫你整理好。`;
    }

    function getBatchUploadLimits(source = getSelectedBatchSource()) {
        const localRequestMaxBytes = (config?.local_upload_request_max_total_mb || 4) * 1024 * 1024;
        return {
            totalMaxFiles: config?.batch_upload_total_max_files || 200,
            batchSize: source === 'drive'
                ? (config?.batch_upload_batch_size_cloud || config?.batch_upload_max_files_cloud || 20)
                : (config?.batch_upload_batch_size_local || config?.batch_upload_max_files_local || config?.batch_upload_batch_size || config?.batch_upload_max_files || 20),
            localRequestMaxFiles: config?.local_upload_request_max_files || 3,
            localRequestMaxBytes,
            maxFileBytes: source === 'local'
                ? Math.min((config?.batch_upload_max_file_mb || 2) * 1024 * 1024, localRequestMaxBytes)
                : (config?.batch_upload_max_file_mb || 2) * 1024 * 1024,
            maxTotalBytes: (config?.batch_upload_max_total_mb || 4) * 1024 * 1024,
            defaultConcurrency: config?.batch_upload_concurrency || 5,
        };
    }

    function getBatchUploadLimitsHint(source = getSelectedBatchSource()) {
        if (source === 'drive') {
            return config?.batch_upload_limits_cloud_message || `Google 雲端會每 ${getBatchUploadLimits(source).batchSize} 張分成一批送出，全部準備好後再整理人物。`;
        }
        const limits = getBatchUploadLimits(source);
        return config?.batch_upload_limits_local_message || `這台電腦一次可先準備 ${limits.totalMaxFiles} 張，會每 ${limits.batchSize} 張分成一批整理；單檔 ${config?.batch_upload_max_file_mb || 2}MB、合計 ${config?.batch_upload_max_total_mb || 4}MB 以內。`;
    }

    function buildLocalUploadChunks(files) {
        const limits = getBatchUploadLimits('local');
        const chunks = [];
        let currentChunk = [];
        let currentBytes = 0;
        files.forEach(file => {
            const wouldExceedCount = currentChunk.length >= limits.localRequestMaxFiles;
            const wouldExceedBytes = currentChunk.length > 0 && currentBytes + file.size > limits.localRequestMaxBytes;
            if (wouldExceedCount || wouldExceedBytes) {
                chunks.push(currentChunk);
                currentChunk = [];
                currentBytes = 0;
            }
            currentChunk.push(file);
            currentBytes += file.size;
        });
        if (currentChunk.length > 0) chunks.push(currentChunk);
        return chunks;
    }

    function syncBatchConcurrencyInput(source = getSelectedBatchSource()) {
        const cap = getBatchConcurrencyCap(source);
        batchConcurrency.max = String(cap);
        batchConcurrency.min = '1';
        if (batchConcurrencyHint) {
            batchConcurrencyHint.textContent = getBatchConcurrencyHint(source);
        }
        const normalized = clampNumberValue(batchConcurrency.value, {
            fallback: 1,
            minimum: 1,
            maximum: cap,
            integer: true,
        });
        if (String(normalized) !== String(batchConcurrency.value)) {
            logBatchValidationFailure({
                scope: source === 'drive' ? 'cloud_batch' : 'local_batch',
                field: 'concurrency',
                input: batchConcurrency.value,
                normalized,
                minimum: 1,
                maximum: cap,
                reason: 'input_normalized',
            });
            batchConcurrency.value = String(normalized);
        }
    }

    function syncBatchUploadLimitsHint(source = getSelectedBatchSource()) {
        const limitsEl = document.getElementById('batch-upload-limits');
        if (!limitsEl) return;
        limitsEl.textContent = getBatchUploadLimitsHint(source);
    }

    function validateBatchConcurrency(source, value) {
        const cap = getBatchConcurrencyCap(source);
        if (!Number.isFinite(value) || value < 1 || value > cap) {
            logBatchValidationFailure({
                scope: source === 'drive' ? 'cloud_batch' : 'local_batch',
                field: 'concurrency',
                input: value,
                minimum: 1,
                maximum: cap,
                reason: 'out_of_range',
            });
            throw new Error(`這次先幫我一次看 1 到 ${cap} 張就好，整理起來會比較穩。`);
        }
    }

    // === Batch Source Switching ===
    batchSourceRadios.forEach(radio => {
        radio.addEventListener('change', (e) => {
            if (e.target.value === 'local') {
                localBatchInputs.classList.remove('hidden');
                driveBatchInputs.classList.add('hidden');
            } else {
                localBatchInputs.classList.add('hidden');
                driveBatchInputs.classList.remove('hidden');
                tryFetchServerToken();
            }
            syncBatchUploadLimitsHint(e.target.value);
            syncBatchConcurrencyInput(e.target.value);
        });
    });

    batchConcurrency?.addEventListener('change', () => {
        syncBatchConcurrencyInput();
    });
    batchConcurrency?.addEventListener('blur', () => {
        syncBatchConcurrencyInput();
    });

    googleLoginBtn.addEventListener('click', () => {
        // 這裡導向後端的 OAuth 入口 (預計實作為 /auth/google)
        window.location.href = '/auth/google';
    });

    async function checkLoginStatus() {
        try {
            const res = await fetch('/api/user/me');
            const data = await res.json();

            const guestArea = document.getElementById('drive-guest-area');
            const userArea = document.getElementById('drive-user-area');

            if (data.logged_in) {
                guestArea.classList.add('hidden');
                userArea.classList.remove('hidden');
                document.getElementById('user-avatar').src = data.picture || '';
                document.getElementById('user-name').textContent = data.name || '已登入';
                document.getElementById('user-email').textContent = data.email || '';
            } else {
                guestArea.classList.remove('hidden');
                userArea.classList.add('hidden');
            }
        } catch (err) {
            console.warn("Failed to check login status:", err);
        }
    }

    checkLoginStatus();

    // Viewer Elements
    const splitViewer = document.getElementById('split-viewer');
    const emptyState = document.getElementById('empty-state');
    const loadingOverlay = document.getElementById('loading-overlay');
    const originalImg = document.getElementById('original-img');
    const annotatedImg = document.getElementById('annotated-img');
    const pageIndicator = document.getElementById('page-indicator');
    const prevBtn = document.getElementById('prev-btn');
    const nextBtn = document.getElementById('next-btn');
    const closeFsBtn = document.getElementById('close-fs-btn');
    const viewerImages = document.querySelector('.viewer-images');

    // Stats Elements
    const safetyBadge = document.getElementById('safety-badge');
    const fileNameDisplay = document.getElementById('file-name-display');
    const moderationReason = document.getElementById('moderation-reason');
    const faceCount = document.getElementById('face-count');
    const strapCount = document.getElementById('strap-count');
    const strapColor = document.getElementById('strap-color');

    // Toast
    const toastEl = document.getElementById('toast');

    // === Fullscreen Listeners ===
    // Clicking image-box is handled via onclick in HTML.
    // Close button handled via onclick in HTML.
    // Escape key to exit fullscreen.
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closePhotoPeopleModal();
            toggleFullscreen(false);
        }
    });

    // Sync CSS class if user exits via ESC (native browser trigger)
    document.addEventListener('fullscreenchange', () => {
        if (!document.fullscreenElement) {
            splitViewer.classList.remove('fullscreen-mode');
            document.body.style.overflow = '';
        }
    });
    document.addEventListener('webkitfullscreenchange', () => {
        if (!document.webkitFullscreenElement) {
            splitViewer.classList.remove('fullscreen-mode');
            document.body.style.overflow = '';
        }
    });

    // State
    let currentBatchResults = [];
    let currentIndex = 0;
    let batchMode = null; // 'local' | 'drive'
    let batchOverviewActive = false;
    let batchOverviewMode = localStorage.getItem('batchOverviewMode') || 'thumbnail';
    let currentTempFolder = null;
    let currentFaceClusters = [];
    let selectedFaceClusterId = null;
    let faceClusteringInfo = null;
    let focusedFaceEvidence = null;
    const faceClusterUi = {
        expanded: new Set(),
        selectedEvidenceIndexes: new Map(),
    };
    let relationshipViewMode = 'people';
    let photoPeopleAssignments = {};
    const photoClusterUi = { expanded: new Set() };

    let batchSelectedFiles = [];
    let batchFailureDetails = [];

    const downloadBatchResultsBtn = document.getElementById('download-batch-results-btn');
    const saveDriveResultsBtn = document.getElementById('save-drive-results-btn');
    const includeAnnotatedDownload = document.getElementById('include-annotated-download');
    let config = null;

    function publicClassificationWasRun(item) {
        const analysis = item?.result || item || {};
        return analysis.public_classification_performed !== false;
    }

    function isBusy() {
        return localOperationBusy || loadingControlStates !== null;
    }

    function getFaceClusterDefaults() {
        return {
            eps: Number(config?.face_cluster_default_eps ?? 0.9),
            minSamples: Number(config?.face_cluster_default_min_samples ?? 2),
            epsMin: Number(config?.face_cluster_eps_min ?? 0.05),
            epsMax: Number(config?.face_cluster_eps_max ?? 1.5),
            minSamplesMax: getBatchUploadLimits().batchSize,
        };
    }

    function clampNumberValue(rawValue, { fallback, minimum, maximum, integer = false }) {
        let value = integer ? parseInt(rawValue, 10) : Number(rawValue);
        if (!Number.isFinite(value)) value = fallback;
        if (integer) value = Math.round(value);
        value = Math.max(minimum, value);
        value = Math.min(maximum, value);
        return value;
    }

    function syncFaceClusterSummary() {
        const summary = document.getElementById('cluster-settings-summary');
        if (!summary) return;
        const eps = Number(faceClusterEpsInput.value || getFaceClusterDefaults().eps);
        const minSamples = Number(faceClusterMinSamplesInput.value || getFaceClusterDefaults().minSamples);
        summary.textContent = `DBSCAN: eps ${eps.toFixed(2)}, min_samples ${minSamples}`;
    }

    function applyFaceClusterDefaults() {
        const defaults = getFaceClusterDefaults();
        faceClusterEpsInput.min = String(defaults.epsMin);
        faceClusterEpsInput.max = String(defaults.epsMax);
        faceClusterEpsInput.value = String(defaults.eps);
        faceClusterMinSamplesInput.min = '1';
        faceClusterMinSamplesInput.max = String(defaults.minSamplesMax);
        faceClusterMinSamplesInput.value = String(defaults.minSamples);
        syncFaceClusterSummary();
    }

    function normalizeFaceClusterInputs() {
        const defaults = getFaceClusterDefaults();
        const normalizedEps = clampNumberValue(faceClusterEpsInput.value, {
            fallback: defaults.eps,
            minimum: defaults.epsMin,
            maximum: defaults.epsMax,
        });
        if (String(normalizedEps) !== String(faceClusterEpsInput.value)) {
            logBatchValidationFailure({
                scope: 'face_cluster',
                field: 'eps',
                input: faceClusterEpsInput.value,
                normalized: normalizedEps,
                minimum: defaults.epsMin,
                maximum: defaults.epsMax,
                reason: 'input_normalized',
            });
            faceClusterEpsInput.value = String(normalizedEps);
        }
        const normalizedMinSamples = clampNumberValue(faceClusterMinSamplesInput.value, {
            fallback: defaults.minSamples,
            minimum: 1,
            maximum: defaults.minSamplesMax,
            integer: true,
        });
        if (String(normalizedMinSamples) !== String(faceClusterMinSamplesInput.value)) {
            logBatchValidationFailure({
                scope: 'face_cluster',
                field: 'min_samples',
                input: faceClusterMinSamplesInput.value,
                normalized: normalizedMinSamples,
                minimum: 1,
                maximum: defaults.minSamplesMax,
                reason: 'input_normalized',
            });
            faceClusterMinSamplesInput.value = String(normalizedMinSamples);
        }
        syncFaceClusterSummary();
    }

    function syncProcessingScope() {
        const runPublic = batchRunPublic ? batchRunPublic.checked : false;
        const runFaces = batchRunFaces ? batchRunFaces.checked : true;
        const noFeatureSelected = !runPublic && !runFaces;
        const clusterPanel = document.getElementById('cluster-settings-panel');
        const publicPanel = document.getElementById('batch-public-settings-panel');

        batchRunPublic?.closest('.processing-option')?.classList.toggle('selected', runPublic);
        batchRunFaces?.closest('.processing-option')?.classList.toggle('selected', runFaces);
        clusterPanel.classList.toggle('is-inactive', !runFaces);
        publicPanel?.classList.toggle('is-inactive', !runPublic);
        clusterPanel.querySelectorAll('button, input').forEach(control => { control.disabled = !runFaces; });
        publicPanel?.querySelectorAll('button, input').forEach(control => { control.disabled = !runPublic; });
        const editMemoryBtn = document.getElementById('btn-edit-collaborative-memory');
        if (editMemoryBtn) editMemoryBtn.disabled = batchRunPublic ? !runPublic : false;
        const addMemoryLocalBtn = document.getElementById('btn-add-memory-local');
        if (addMemoryLocalBtn) addMemoryLocalBtn.disabled = batchRunPublic ? !runPublic : false;

        analyzeBatchBtn.disabled = noFeatureSelected;
        if (processingScopeHint) {
            processingScopeHint.classList.toggle('error', noFeatureSelected);
            if (noFeatureSelected) {
                processingScopeHint.textContent = '至少選擇一項功能才能開始。';
                analyzeBatchBtn.textContent = '請先選擇要執行的功能';
            } else if (runPublic && runFaces) {
                processingScopeHint.textContent = '兩項都會執行，可個別關閉。';
                analyzeBatchBtn.textContent = '開始判定並分群';
            } else if (runPublic) {
                processingScopeHint.textContent = '只判定可公開性，不執行人臉分群。';
                analyzeBatchBtn.textContent = '開始判定可公開性';
            } else {
                processingScopeHint.textContent = '只做人臉分群，不呼叫可公開性判定。';
                analyzeBatchBtn.textContent = '開始人臉分群';
            }
        } else {
            analyzeBatchBtn.textContent = noFeatureSelected
                ? '請先選擇要執行的功能'
                : runFaces
                    ? '開始人臉分群'
                    : '開始看這些照片';
        }
    }

    [batchRunPublic, batchRunFaces].filter(Boolean).forEach(input => input.addEventListener('change', syncProcessingScope));
    syncProcessingScope();
    faceClusterEpsInput?.addEventListener('change', normalizeFaceClusterInputs);
    faceClusterEpsInput?.addEventListener('blur', normalizeFaceClusterInputs);
    faceClusterMinSamplesInput?.addEventListener('change', normalizeFaceClusterInputs);
    faceClusterMinSamplesInput?.addEventListener('blur', normalizeFaceClusterInputs);

    function readFaceClusterParams() {
        const defaults = getFaceClusterDefaults();
        const eps = Number(faceClusterEpsInput.value || defaults.eps);
        const minSamples = Number(faceClusterMinSamplesInput.value || defaults.minSamples);
        if (!Number.isFinite(eps) || eps < defaults.epsMin || eps > defaults.epsMax) {
            logBatchValidationFailure({
                scope: 'face_cluster',
                field: 'eps',
                input: eps,
                minimum: defaults.epsMin,
                maximum: defaults.epsMax,
                reason: 'out_of_range',
            });
            throw new Error(`分群設定先幫我放在 ${defaults.epsMin} 到 ${defaults.epsMax} 之間，這樣比較穩。`);
        }
        if (!Number.isInteger(minSamples) || minSamples < 1 || minSamples > defaults.minSamplesMax) {
            logBatchValidationFailure({
                scope: 'face_cluster',
                field: 'min_samples',
                input: minSamples,
                minimum: 1,
                maximum: defaults.minSamplesMax,
                reason: 'out_of_range',
            });
            throw new Error(`分群設定先幫我放在 1 到 ${defaults.minSamplesMax} 之間，這樣比較容易整理得準。`);
        }
        return { eps, minSamples };
    }

    function initializePhotoPeopleAssignments() {
        photoPeopleAssignments = PhotoRelationships.createAssignments(currentFaceClusters);
        currentBatchResults.forEach(item => {
            const fileName = item.file_name || item.file;
            if (fileName && !(fileName in photoPeopleAssignments)) photoPeopleAssignments[fileName] = [];
        });
    }

    function setCurrentFaceClusters(clusters) {
        currentFaceClusters = Array.isArray(clusters) ? clusters : [];
        selectedFaceClusterId = currentFaceClusters[0]?.cluster_id || null;
        initializePhotoPeopleAssignments();
    }

    function validateBatchFiles(files, source = getSelectedBatchSource()) {
        const limits = getBatchUploadLimits(source);
        if (source === 'local' && files.length > limits.totalMaxFiles) {
            logBatchValidationFailure({
                scope: source === 'drive' ? 'cloud_batch' : 'local_batch',
                field: 'file_count',
                input: files.length,
                minimum: 1,
                maximum: limits.totalMaxFiles,
                reason: 'above_maximum',
            });
            return `這次先幫我挑 1 到 ${limits.totalMaxFiles} 張照片就好，我會分批慢慢整理。`;
        }
        const oversized = files.find(file => file.size > limits.maxFileBytes);
        if (oversized) {
            logBatchValidationFailure({
                scope: source === 'drive' ? 'cloud_batch' : 'local_batch',
                field: 'file_size_bytes',
                input: oversized.size,
                maximum: limits.maxFileBytes,
                reason: 'file_too_large',
                fileName: oversized.name,
            });
            return `${oversized.name} 有點太大了，先幫我換成 ${Math.round(limits.maxFileBytes / 1024 / 1024)}MB 以內的版本。`;
        }
        const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
        if (totalBytes > limits.maxTotalBytes) {
            logBatchValidationFailure({
                scope: source === 'drive' ? 'cloud_batch' : 'local_batch',
                field: 'total_size_bytes',
                input: totalBytes,
                maximum: limits.maxTotalBytes,
                reason: 'total_size_too_large',
            });
            return '這批照片有點太大了，先少放一點，整理起來會比較順。';
        }
        return null;
    }

    function getFailureReason(item) {
        const reason = item?.error || item?.detail || item?.message || '未提供失敗原因';
        return String(reason).trim() || '未提供失敗原因';
    }

    function recordBatchFailure(item) {
        const fileName = item?.file_name || item?.file || '未命名檔案';
        const reason = getFailureReason(item);
        const detail = { file: fileName, reason };
        batchFailureDetails.push(detail);
        console.error(`[Batch Failure] ${fileName}: ${reason}`, item);
        showToast(`${fileName} 沒看成：${reason}`, 'error');
    }

    function flushBatchFailureSummary() {
        if (batchFailureDetails.length === 0) return;
        console.groupCollapsed(`Batch failure details (${batchFailureDetails.length})`);
        batchFailureDetails.forEach((detail, index) => {
            console.error(`${index + 1}. ${detail.file}: ${detail.reason}`);
        });
        console.groupEnd();
    }

    function selectBatchFiles(files) {
        const images = Array.from(files).filter(file => file.type.startsWith('image/'));
        const error = validateBatchFiles(images, 'local');
        batchCloudGuidance.classList.toggle('hidden', !error);
        if (error) {
            batchSelectedFiles = [];
            batchFileSummary.classList.add('hidden');
            showToast(`${error}，請減少檔案或改用 Google 雲端`, 'error');
            return;
        }
        batchSelectedFiles = images;
        const totalMb = images.reduce((sum, file) => sum + file.size, 0) / 1024 / 1024;
        batchFileSummary.innerHTML = `<strong>已選 ${images.length} 張</strong>，合計 ${totalMb.toFixed(2)} MB`;
        batchFileSummary.classList.toggle('hidden', images.length === 0);
    }

    batchDropZone.addEventListener('click', () => batchFileInput.click());
    batchFileInput.addEventListener('change', () => selectBatchFiles(batchFileInput.files));
    batchDropZone.addEventListener('dragover', event => {
        event.preventDefault();
        batchDropZone.classList.add('dragover');
    });
    batchDropZone.addEventListener('dragleave', () => batchDropZone.classList.remove('dragover'));
    batchDropZone.addEventListener('drop', event => {
        event.preventDefault();
        batchDropZone.classList.remove('dragover');
        selectBatchFiles(event.dataTransfer.files);
    });
    document.getElementById('switch-to-drive-btn').addEventListener('click', () => {
        const driveRadio = document.querySelector('input[name="batch-source"][value="drive"]');
        driveRadio.checked = true;
        driveRadio.dispatchEvent(new Event('change'));
    });
    batchConcurrency.value = String(getBatchUploadLimits().defaultConcurrency);
    faceClusterEpsInput.addEventListener('input', syncFaceClusterSummary);
    faceClusterMinSamplesInput.addEventListener('input', syncFaceClusterSummary);

    window.__toggleClusterSettings = function () {
        const body = document.getElementById('cluster-settings-body');
        const arrow = document.getElementById('cluster-settings-arrow');
        const collapsed = body.classList.toggle('hidden');
        arrow.textContent = collapsed ? '▼' : '▲';
    };

    document.getElementById('btn-toggle-cluster-settings').addEventListener('click', window.__toggleClusterSettings);

    // === Color Rules ===
    const DEFAULT_COLOR_SWATCHES = [
        { name: "藍色", keywords: ["藍"], hex: "#1E56D6", rgb: [30, 86, 214], safe: true },
        { name: "深藍色", keywords: ["深藍", "navy"], hex: "#003087", rgb: [0, 48, 135], safe: true },
        { name: "青色", keywords: ["青"], hex: "#00C0C0", rgb: [0, 192, 192], safe: false },
        { name: "紅色", keywords: ["紅"], hex: "#DC2626", rgb: [220, 38, 38], safe: true },
        { name: "橙色", keywords: ["橙", "橘"], hex: "#EA580C", rgb: [234, 88, 12], safe: true },
        { name: "黃色", keywords: ["黃"], hex: "#D97706", rgb: [217, 119, 6], safe: true },
        { name: "深綠色", keywords: ["深綠"], hex: "#1A4731", rgb: [26, 71, 49], safe: false },
        { name: "綠色", keywords: ["綠"], hex: "#16A34A", rgb: [22, 163, 74], safe: true },
        { name: "紫色", keywords: ["紫"], hex: "#7C3AED", rgb: [124, 58, 237], safe: true },
        { name: "粉色", keywords: ["粉", "桃"], hex: "#EC4899", rgb: [236, 72, 153], safe: true },
        { name: "黑色", keywords: ["黑"], hex: "#1A1A1A", rgb: [26, 26, 26], safe: true },
        { name: "白色", keywords: ["白"], hex: "#F0F0F0", rgb: [240, 240, 240], safe: true },
        { name: "灰色", keywords: ["灰"], hex: "#6B7280", rgb: [107, 114, 128], safe: true },
    ];

    let colorSwatches = JSON.parse(localStorage.getItem('colorSwatches') || 'null') || DEFAULT_COLOR_SWATCHES.map(s => ({ ...s }));

    function saveColorSwatches() {
        localStorage.setItem('colorSwatches', JSON.stringify(colorSwatches));
    }

    function renderColorSwatches() {
        const grid = document.getElementById('color-swatches-grid');
        if (!grid) return;
        grid.innerHTML = colorSwatches.map((s, idx) => `
            <div class="color-swatch ${s.safe ? 'swatch-safe' : 'swatch-unsafe'}" data-idx="${idx}"
                 title="${s.name}\n${s.hex}\nRGB(${s.rgb.join(', ')})">
                <div class="swatch-color" style="background:${s.hex}"></div>
                <span class="swatch-name">${s.name}</span>
                <span class="swatch-status">${s.safe ? '可公開' : '不可'}</span>
            </div>`
        ).join('');
        grid.querySelectorAll('.color-swatch').forEach(el => {
            el.addEventListener('click', () => {
                const idx = parseInt(el.dataset.idx);
                colorSwatches[idx].safe = !colorSwatches[idx].safe;
                saveColorSwatches();
                renderColorSwatches();
            });
        });
    }

    window.__toggleColorRules = function () {
        const body = document.getElementById('color-rules-body');
        const arrow = document.getElementById('color-rules-arrow');
        const collapsed = body.classList.toggle('hidden');
        arrow.textContent = collapsed ? '▼' : '▲';
        if (!collapsed) renderColorSwatches();
    };

    window.__resetColorRules = function () {
        colorSwatches = DEFAULT_COLOR_SWATCHES.map(s => ({ ...s }));
        saveColorSwatches();
        renderColorSwatches();
    };

    applyFaceClusterDefaults();

    // Review Elements
    const decisionButtons = document.getElementById('decision-buttons');
    const btnSetSafe = document.getElementById('btn-set-safe');
    const btnSetPending = document.getElementById('btn-set-pending');
    const btnSetUnsafe = document.getElementById('btn-set-unsafe');
    const reviewSummary = document.getElementById('review-summary');
    const reviewList = document.getElementById('review-list');
    const reviewSafeCount = document.getElementById('review-safe-count');
    const reviewPendingCount = document.getElementById('review-pending-count');
    const reviewUnsafeCount = document.getElementById('review-unsafe-count');
    const finalizeBtn = document.getElementById('finalize-btn');

    // === Helpers ===
    function toggleFullscreen(force) {
        const isCurrentlyFS = splitViewer.classList.contains('fullscreen-mode');
        const shouldBeFS = (force !== undefined) ? force : !isCurrentlyFS;

        if (shouldBeFS) {
            splitViewer.classList.add('fullscreen-mode');
            document.body.style.overflow = 'hidden';

            // Try to enter native browser fullscreen if possible
            try {
                if (splitViewer.requestFullscreen) {
                    splitViewer.requestFullscreen();
                } else if (splitViewer.webkitRequestFullscreen) {
                    splitViewer.webkitRequestFullscreen();
                }
            } catch (err) {
                console.warn("Native fullscreen failed:", err);
            }
        } else {
            resetZoom();
            splitViewer.classList.remove('fullscreen-mode');
            document.body.style.overflow = '';

            // Exit native browser fullscreen if we are in it
            try {
                if (document.fullscreenElement || document.webkitFullscreenElement) {
                    if (document.exitFullscreen) {
                        document.exitFullscreen();
                    } else if (document.webkitExitFullscreen) {
                        document.webkitExitFullscreen();
                    }
                }
            } catch (err) {
                console.warn("Native exit fullscreen failed:", err);
            }
        }
    }

    // Expose for HTML onclick attributes
    window.__toggleFullscreen = () => toggleFullscreen();
    window.__exitFullscreen = () => toggleFullscreen(false);

    // === Fullscreen Zoom + Pan ===
    let zoomScale = 1;
    let panX = 0, panY = 0;
    const ZOOM_MIN = 1, ZOOM_MAX = 5;

    function applyZoom() {
        viewerImages.style.transformOrigin = 'center center';
        viewerImages.style.transform = (zoomScale > 1 || panX !== 0 || panY !== 0)
            ? `translate(${panX.toFixed(1)}px, ${panY.toFixed(1)}px) scale(${zoomScale.toFixed(3)})`
            : '';
        splitViewer.classList.toggle('zoomed', zoomScale > 1);
    }

    function clampPan() {
        if (zoomScale <= 1) { panX = 0; panY = 0; return; }
        const maxX = viewerImages.offsetWidth * (zoomScale - 1) / 2;
        const maxY = viewerImages.offsetHeight * (zoomScale - 1) / 2;
        panX = Math.min(Math.max(panX, -maxX), maxX);
        panY = Math.min(Math.max(panY, -maxY), maxY);
    }

    function resetZoom() {
        zoomScale = 1; panX = 0; panY = 0;
        applyZoom();
    }

    // Desktop: wheel zoom
    viewerImages.addEventListener('wheel', (e) => {
        if (!splitViewer.classList.contains('fullscreen-mode')) return;
        e.preventDefault();
        zoomScale = Math.min(Math.max(zoomScale * (e.deltaY < 0 ? 1.15 : 1 / 1.15), ZOOM_MIN), ZOOM_MAX);
        clampPan();
        applyZoom();
    }, { passive: false });

    // Desktop: mouse drag to pan
    let isPanning = false, panLastX = 0, panLastY = 0;

    viewerImages.addEventListener('mousedown', (e) => {
        if (!splitViewer.classList.contains('fullscreen-mode') || zoomScale <= 1) return;
        isPanning = true;
        panLastX = e.clientX;
        panLastY = e.clientY;
        splitViewer.classList.add('panning');
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        panX += e.clientX - panLastX;
        panY += e.clientY - panLastY;
        panLastX = e.clientX;
        panLastY = e.clientY;
        clampPan();
        applyZoom();
    });

    document.addEventListener('mouseup', () => {
        if (!isPanning) return;
        isPanning = false;
        splitViewer.classList.remove('panning');
    });

    // Mobile: pinch-to-zoom + single-finger pan (when zoomed)
    let pinchStartDist = null, pinchStartScale = 1;
    let touchPanActive = false, touchLastX = 0, touchLastY = 0;

    function getPinchDist(touches) {
        const dx = touches[0].clientX - touches[1].clientX;
        const dy = touches[0].clientY - touches[1].clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }

    viewerImages.addEventListener('touchstart', (e) => {
        if (e.touches.length === 2) {
            pinchStartDist = getPinchDist(e.touches);
            pinchStartScale = zoomScale;
            touchPanActive = false;
        } else if (e.touches.length === 1 && zoomScale > 1) {
            touchPanActive = true;
            touchLastX = e.touches[0].clientX;
            touchLastY = e.touches[0].clientY;
        }
    }, { passive: true });

    viewerImages.addEventListener('touchmove', (e) => {
        if (e.touches.length === 2 && pinchStartDist !== null) {
            e.preventDefault();
            zoomScale = Math.min(Math.max(pinchStartScale * getPinchDist(e.touches) / pinchStartDist, ZOOM_MIN), ZOOM_MAX);
            clampPan();
            applyZoom();
        } else if (e.touches.length === 1 && touchPanActive && zoomScale > 1) {
            e.preventDefault();
            panX += e.touches[0].clientX - touchLastX;
            panY += e.touches[0].clientY - touchLastY;
            touchLastX = e.touches[0].clientX;
            touchLastY = e.touches[0].clientY;
            clampPan();
            applyZoom();
        }
    }, { passive: false });

    viewerImages.addEventListener('touchend', (e) => {
        if (e.touches.length < 2) pinchStartDist = null;
        if (e.touches.length === 0) touchPanActive = false;
    }, { passive: true });

    function showToast(msg, type = 'success') {
        toastEl.textContent = msg;
        toastEl.className = `toast show ${type}`;
        setTimeout(() => toastEl.classList.remove('show'), 3000);
    }

    function logBatchValidationFailure(payload) {
        console.warn('[batch-validation]', {
            at: new Date().toISOString(),
            ...payload,
        });
    }

    function logBatchRequestFailure(payload) {
        console.error('[batch-request-failure]', {
            at: new Date().toISOString(),
            ...payload,
        });
    }

    let loadingControlStates = null;
    const sharedBusyKey = 'photoIdentifier.sharedBusy';
    const tabId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    const sharedBusyTtlMs = 120000;
    let sharedBusyHeartbeat = null;
    let localOperationBusy = false;
    let releaseWebLock = null;

    function readSharedBusy() {
        try {
            const value = JSON.parse(localStorage.getItem(sharedBusyKey) || 'null');
            if (!value || value.expiresAt <= Date.now()) {
                if (value) localStorage.removeItem(sharedBusyKey);
                return null;
            }
            return value;
        } catch (error) {
            localStorage.removeItem(sharedBusyKey);
            return null;
        }
    }

    function writeSharedBusy(label) {
        localStorage.setItem(sharedBusyKey, JSON.stringify({
            tabId,
            label,
            expiresAt: Date.now() + sharedBusyTtlMs,
        }));
    }

    async function beginSharedBusy(label) {
        if (isBusy()) return false;
        if (navigator.locks) {
            const acquired = await new Promise(resolve => {
                navigator.locks.request(sharedBusyKey, { ifAvailable: true }, lock => {
                    resolve(Boolean(lock));
                    if (!lock) return undefined;
                    return new Promise(release => { releaseWebLock = release; });
                }).catch(() => resolve(null));
            });
            if (acquired === false) {
                showToast('另一個頁籤正在處理照片，請稍候', 'error');
                return false;
            }
            // null means Web Locks failed; continue with the storage lease fallback.
        }
        const active = readSharedBusy();
        if (active && active.tabId !== tabId) {
            releaseWebLock?.();
            releaseWebLock = null;
            showToast(`另一個頁籤正在${active.label || '處理照片'}，請稍候`, 'error');
            return false;
        }
        localOperationBusy = true;
        writeSharedBusy(label);
        sharedBusyHeartbeat = window.setInterval(() => writeSharedBusy(label), 20000);
        return true;
    }

    function endSharedBusy() {
        localOperationBusy = false;
        if (sharedBusyHeartbeat) window.clearInterval(sharedBusyHeartbeat);
        sharedBusyHeartbeat = null;
        const active = readSharedBusy();
        if (active?.tabId === tabId) localStorage.removeItem(sharedBusyKey);
        releaseWebLock?.();
        releaseWebLock = null;
    }

    function syncRemoteBusyState() {
        if (localOperationBusy) return;
        const active = readSharedBusy();
        const remoteBusy = Boolean(active && active.tabId !== tabId);
        showLoading(remoteBusy);
        if (remoteBusy) {
            document.getElementById('loading-text').textContent =
                `另一個頁籤正在${active.label || '處理照片'}，完成後即可繼續…`;
        } else {
            document.getElementById('loading-text').textContent = '正在一張一張看過去…';
        }
    }

    function handlePollError(error) {
        console.warn('Busy state sync failed:', error);
    }

    window.addEventListener('storage', event => {
        if (event.key === sharedBusyKey) syncRemoteBusyState();
    });
    window.setInterval(() => {
        try {
            syncRemoteBusyState();
        } catch (error) {
            handlePollError(error);
        }
    }, 5000);
    window.addEventListener('pagehide', endSharedBusy);

    function showLoading(show) {
        if (show) {
            if (!loadingControlStates) {
                loadingControlStates = new Map();
                document.querySelectorAll('button, input, select, textarea').forEach(control => {
                    if (control.dataset.allowBusy === 'true') return;
                    loadingControlStates.set(control, control.disabled);
                    control.disabled = true;
                });
            }
            document.body.classList.add('app-busy');
            document.body.setAttribute('aria-busy', 'true');
            loadingOverlay.classList.remove('hidden');
        } else {
            loadingOverlay.classList.add('hidden');
            document.body.classList.remove('app-busy');
            document.body.removeAttribute('aria-busy');
            loadingControlStates?.forEach((wasDisabled, control) => {
                control.disabled = wasDisabled;
            });
            loadingControlStates = null;
        }
    }

    syncRemoteBusyState();

    // === Tab Switching ===
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            modeContents.forEach(c => c.classList.remove('active'));

            btn.classList.add('active');
            document.getElementById(`${btn.dataset.target}-mode`).classList.add('active');

            if (btn.dataset.target === 'batch' && currentBatchResults.length > 0) {
                // 切回批量頁時，恢復之前的批量總覽
                showBatchOverview();
            } else {
                splitViewer.classList.add('hidden');
                emptyState.classList.remove('hidden');
                document.getElementById('batch-overview').classList.add('hidden');
            }
        });
    });

    // === Single Mode Handling ===
    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            handleSingleFileSelect(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) {
            handleSingleFileSelect(e.target.files[0]);
        }
    });

    function handleSingleFileSelect(file) {
        if (!file.type.startsWith('image/')) {
            showToast('請上傳圖片檔案', 'error');
            return;
        }
        singleSelectedFile = file;
        dropZone.querySelector('p').textContent = `已選擇：${file.name}`;
        analyzeSingleBtn.disabled = false;

        // Preview original instantly
        const reader = new FileReader();
        reader.onload = e => {
            emptyState.classList.add('hidden');
            splitViewer.classList.remove('hidden');
            originalImg.src = e.target.result;
            annotatedImg.src = '';
            setPlaceholderStats(file.name);
            pageIndicator.textContent = '1 / 1';
        };
        reader.readAsDataURL(file);
    }

    function setPlaceholderStats(filename) {
        fileNameDisplay.textContent = filename;
        safetyBadge.textContent = '等待分析...';
        safetyBadge.className = 'status-badge';
        moderationReason.textContent = '點擊「開始辨識單圖」送出請求';
        faceCount.textContent = '-';
        strapCount.textContent = '-';
        strapColor.textContent = '-';
    }

    function getFaceFocusOverlay() {
        const imageBox = annotatedImg.closest('.image-box');
        if (!imageBox) return null;
        let overlay = imageBox.querySelector('.face-focus-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.className = 'face-focus-overlay hidden';
            overlay.setAttribute('aria-hidden', 'true');
            imageBox.appendChild(overlay);
        }
        return overlay;
    }

    function renderFaceFocusOverlay() {
        const overlay = getFaceFocusOverlay();
        const bbox = Array.isArray(focusedFaceEvidence?.bbox) ? focusedFaceEvidence.bbox.map(Number) : null;
        if (!overlay || !bbox || bbox.length !== 4 || bbox.some(value => !Number.isFinite(value)) || !annotatedImg.naturalWidth || !annotatedImg.naturalHeight) {
            overlay?.classList.add('hidden');
            return;
        }

        const [x1, y1, x2, y2] = bbox;
        const imageRect = annotatedImg.getBoundingClientRect();
        const parentRect = annotatedImg.closest('.image-box').getBoundingClientRect();
        const left = imageRect.left - parentRect.left + (x1 / annotatedImg.naturalWidth) * imageRect.width;
        const top = imageRect.top - parentRect.top + (y1 / annotatedImg.naturalHeight) * imageRect.height;
        const width = ((x2 - x1) / annotatedImg.naturalWidth) * imageRect.width;
        const height = ((y2 - y1) / annotatedImg.naturalHeight) * imageRect.height;

        overlay.style.left = `${left}px`;
        overlay.style.top = `${top}px`;
        overlay.style.width = `${width}px`;
        overlay.style.height = `${height}px`;
        overlay.classList.remove('hidden');
    }

    function clearFaceFocusOverlay() {
        focusedFaceEvidence = null;
        getFaceFocusOverlay()?.classList.add('hidden');
    }

    annotatedImg.addEventListener('load', renderFaceFocusOverlay);
    window.addEventListener('resize', renderFaceFocusOverlay);

    analyzeSingleBtn.addEventListener('click', async () => {
        if (!singleSelectedFile) return;
        if (!(await beginSharedBusy('辨識照片'))) return;

        showLoading(true);
        const formData = new FormData();
        formData.append('file', singleSelectedFile);
        formData.append('color_rules_json', JSON.stringify(colorSwatches));
        if (window._collaborativeMemories && window._collaborativeMemories.single) {
            formData.append('collaborative_memory', window._collaborativeMemories.single);
        }

        try {
            const res = await fetch('/analyze_with_image/', {
                method: 'POST',
                body: formData
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || '伺服器錯誤');
            }

            const data = await res.json();

            // set annotated image
            clearFaceFocusOverlay();
            annotatedImg.src = 'data:image/jpeg;base64,' + data.drawn_image_b64;

            // update stats
            updateStatsUI(singleSelectedFile.name, data.analysis);
            showToast('看完這張了');

        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            showLoading(false);
            endSharedBusy();
        }
    });

    function updateStatsUI(filename, analysis) {
        fileNameDisplay.textContent = filename;

        if (analysis.public_classification_performed === false) {
            safetyBadge.textContent = '未判定';
            safetyBadge.className = 'status-badge status-pending';
            moderationReason.textContent = analysis.moderation_reason || '本次未執行可公開性判定';
            faceCount.textContent = '-';
            strapCount.textContent = '-';
            strapColor.textContent = '-';
            return;
        }

        const status = analysis.moderation_status || (analysis.is_safe_for_public ? 'public' : 'private');
        if (status === 'public') {
            safetyBadge.textContent = '可以分享';
            safetyBadge.className = 'status-badge status-safe';
        } else if (status === 'pending') {
            safetyBadge.textContent = '之後再看';
            safetyBadge.className = 'status-badge status-pending';
        } else {
            safetyBadge.textContent = '先留著';
            safetyBadge.className = 'status-badge status-unsafe';
        }

        moderationReason.textContent = analysis.moderation_reason;
        faceCount.textContent = analysis.face_bboxes ? analysis.face_bboxes.length : 0;
        strapCount.textContent = analysis.strap_bboxes ? analysis.strap_bboxes.length : 0;
        strapColor.textContent = analysis.strap_color || '無';
    }

    function updateOverrideIndicator(data) {
        const existing = document.getElementById('override-indicator');
        if (existing) existing.remove();
        if (data && data.user_decision && data.ai_decision && data.user_decision !== data.ai_decision) {
            const badge = document.createElement('span');
            badge.id = 'override-indicator';
            badge.className = 'override-badge';
            badge.textContent = '✏️ 你改過';
            safetyBadge.parentElement.appendChild(badge);
        }
    }


    // === DOM Progress Elements ===
    const progressFill = document.getElementById('progress-fill');
    const progressPercent = document.getElementById('progress-percent');
    const progressCount = document.getElementById('progress-count');
    const streamSuccessEl = document.getElementById('stream-success-count');
    const streamFailedEl = document.getElementById('stream-failed-count');
    const streamPendingEl = document.getElementById('stream-pending-count');
    const loadingTextEl = document.getElementById('loading-text');
    const loadingDetailEl = document.getElementById('loading-detail');
    const loadingCancelBtn = document.getElementById('loading-cancel-btn');
    const progressSuccessLabel = document.querySelector('.stat-item.success .label');
    const progressFailedLabel = document.querySelector('.stat-item.failed .label');
    const progressPendingLabel = document.querySelector('.stat-item.pending .label');
    let currentBatchAbortController = null;
    let currentBatchCancelRequested = false;
    let currentFaceClusterJobId = null;
    const DRIVE_BATCH_POLL_INTERVAL_MS = 1500;
    const BATCH_VIEW_SNAPSHOT_KEY = 'photoIdentifier.batchViewSnapshot';
    let lastBatchStatusPayload = null;

    const FACE_CLUSTER_STAGE_LABELS = {
        starting: '正在準備整理照片中的人物…',
        uploading: '正在把照片送去人臉分類服務…',
        connection_wait: '人臉分類服務回應比較慢，正在等它接上…',
        queued: '正在排隊，準備開始分辨人物…',
        detecting: '正在分辨照片中的人物…',
        clustering: '正在把同一個人整理在一起…',
        completed: '人物整理完成，正在帶回結果…',
        failed: '人物整理沒有完成',
        cancelled: '人物整理已取消',
    };

    function setLoadingMessage(text, detail = '') {
        if (loadingTextEl) loadingTextEl.textContent = text;
        if (loadingDetailEl) loadingDetailEl.textContent = detail;
    }

    function setLoadingCancelButton({ visible, disabled = false, label = '中止這次整理' }) {
        if (!loadingCancelBtn) return;
        loadingCancelBtn.textContent = label;
        loadingCancelBtn.disabled = disabled;
        loadingCancelBtn.classList.toggle('hidden', !visible);
    }

    async function cancelCurrentBatchOperation() {
        if (!currentBatchAbortController || currentBatchCancelRequested) return;
        currentBatchCancelRequested = true;
        setLoadingCancelButton({ visible: true, disabled: true, label: '正在送出中止…' });

        try {
            if (window._currentSessionId) {
                const response = await fetch(`/batch_sessions/${encodeURIComponent(window._currentSessionId)}/cancel`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_id: currentFaceClusterJobId || null }),
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(payload.detail || payload.message || '中止請求沒有送成功');
                }
                showToast(payload.message || '已送出中止請求');
            }
        } catch (error) {
            showToast(error.message || '中止請求沒有送成功', 'error');
        } finally {
            currentBatchAbortController.abort();
        }
    }

    function setProgressLabels(mode) {
        if (mode === 'faces') {
            if (progressSuccessLabel) progressSuccessLabel.textContent = '已分辨';
            if (progressFailedLabel) progressFailedLabel.textContent = '佇列';
            if (progressPendingLabel) progressPendingLabel.textContent = '還有';
            return;
        }
        if (progressSuccessLabel) progressSuccessLabel.textContent = '看完了';
        if (progressFailedLabel) progressFailedLabel.textContent = '沒看成';
        if (progressPendingLabel) progressPendingLabel.textContent = '還剩';
    }

    function updateProgressUI(current, total, success, failed) {
        setProgressLabels('photos');
        if (total === 0) {
            progressFill.style.width = '0%';
            progressPercent.textContent = '0%';
            progressCount.textContent = '0 / 0';
            streamSuccessEl.textContent = '0';
            streamFailedEl.textContent = '0';
            streamPendingEl.textContent = '0';
            return;
        }
        const percent = Math.round((current / total) * 100);
        progressFill.style.width = percent + '%';
        progressPercent.textContent = percent + '%';
        progressCount.textContent = `${current} / ${total}`;
        streamSuccessEl.textContent = success;
        streamFailedEl.textContent = failed;
        streamPendingEl.textContent = total - current;
    }

    function updateFaceClusterProgressUI(event) {
        setProgressLabels('faces');
        const progress = event.progress || {};
        const completed = Number(progress.completed || 0);
        const total = Number(progress.total || 0);
        const remaining = Math.max(total - completed, 0);
        const percent = total > 0 ? Math.min(Math.round((completed / total) * 100), 100) : 0;
        const queuePosition = Number.isFinite(Number(event.queue_position)) ? Number(event.queue_position) : null;
        const jobsAhead = queuePosition ? Math.max(queuePosition - 1, 0) : null;
        const stageText = FACE_CLUSTER_STAGE_LABELS[event.stage]
            || FACE_CLUSTER_STAGE_LABELS[event.job_status]
            || (String(event.stage || '').startsWith('detecting_batch_')
                ? '正在分批辨識照片中的人物…'
                : '正在整理照片中的人物…');

        progressFill.style.width = percent + '%';
        progressPercent.textContent = percent + '%';
        progressCount.textContent = total > 0 ? `${completed} / ${total}` : '等待中';
        streamSuccessEl.textContent = total > 0 ? completed : 0;
        streamFailedEl.textContent = queuePosition || '—';
        streamPendingEl.textContent = total > 0 ? remaining : '—';
        setLoadingMessage(
            stageText,
            total > 0
                ? `已分辨 ${completed} / ${total} 張。${jobsAhead !== null ? `前面還有 ${jobsAhead} 個工作。` : '全部辨識完後，會一起整理人物。'}`
                : jobsAhead !== null ? `已排入佇列，前面還有 ${jobsAhead} 個工作。` : '已送到人臉分類服務，正在等它回報進度。'
        );
    }

    function wait(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function applyDriveStatusPayload(data) {
        lastBatchStatusPayload = data;
        if (data.session_id) window._currentSessionId = data.session_id;
        const results = Array.isArray(data.results) ? data.results : [];
        currentBatchResults = [];
        batchFailureDetails = [];
        results.forEach(item => {
            if (item.status === 'ok') {
                if (publicClassificationWasRun(item)) {
                    const result = item.result || item;
                    const aiStatus = result.moderation_status || (result.is_safe_for_public ? 'public' : 'private');
                    item.user_decision = item.user_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                    item.ai_decision = item.ai_decision || item.user_decision;
                } else {
                    item.public_classification_performed = false;
                }
                currentBatchResults.push(item);
            } else if (item.status === 'error') {
                recordBatchFailure(item);
            }
        });

        const progress = data.progress || {};
        const total = Number(progress.total || data.total || results.length || 0);
        const completed = Number(progress.completed || (Number(data.success || 0) + Number(data.failed || 0)));
        updateProgressUI(completed, total, Number(data.success || 0), Number(data.failed || 0));

        if (data.face_cluster_progress) {
            if (data.face_cluster_progress.job_id) currentFaceClusterJobId = data.face_cluster_progress.job_id;
            updateFaceClusterProgressUI(data.face_cluster_progress);
        } else if (data.stage === 'queued') {
            setLoadingMessage('正在整理雲端照片…', '已建立工作，正在準備讀取照片。');
        } else if (data.stage === 'photos') {
            setLoadingMessage('正在整理雲端照片…', `已看過 ${completed} / ${total} 張，會一段一段帶回結果。`);
        } else if (data.stage === 'face_uploading') {
            setLoadingMessage('正在把照片送去人臉分類服務…', '照片已讀完，正在建立人物分群工作。');
        }

        if (Array.isArray(data.face_clusters)) {
            setCurrentFaceClusters(data.face_clusters);
        }
        faceClusteringInfo = data.face_clustering || faceClusteringInfo;
        saveBatchViewSnapshot();
    }

    function saveBatchViewSnapshot({ active = Boolean(window._currentSessionId) } = {}) {
        try {
            const snapshot = {
                active,
                sessionId: window._currentSessionId || null,
                batchMode,
                currentIndex,
                batchOverviewActive,
                currentBatchResults,
                currentFaceClusters,
                faceClusteringInfo,
                photoPeopleAssignments,
                lastBatchStatusPayload,
                savedAt: Date.now(),
            };
            sessionStorage.setItem(BATCH_VIEW_SNAPSHOT_KEY, JSON.stringify(snapshot));
        } catch (error) {
            // Large face previews can exceed browser storage; memory state still works.
            console.info('batch view snapshot skipped', error?.name || 'storage_error');
        }
    }

    function restoreBatchViewSnapshot() {
        try {
            const raw = sessionStorage.getItem(BATCH_VIEW_SNAPSHOT_KEY);
            if (!raw) return null;
            const snapshot = JSON.parse(raw);
            if (!snapshot || !Array.isArray(snapshot.currentBatchResults)) return null;
            window._currentSessionId = snapshot.sessionId || null;
            batchMode = snapshot.batchMode || null;
            currentIndex = Number.isInteger(snapshot.currentIndex) ? snapshot.currentIndex : 0;
            batchOverviewActive = Boolean(snapshot.batchOverviewActive);
            currentBatchResults = snapshot.currentBatchResults;
            currentFaceClusters = Array.isArray(snapshot.currentFaceClusters) ? snapshot.currentFaceClusters : [];
            faceClusteringInfo = snapshot.faceClusteringInfo || null;
            photoPeopleAssignments = snapshot.photoPeopleAssignments || {};
            lastBatchStatusPayload = snapshot.lastBatchStatusPayload || null;
            if (lastBatchStatusPayload) {
                updateProgressUI(
                    Number(lastBatchStatusPayload.progress?.completed || 0),
                    Number(lastBatchStatusPayload.progress?.total || currentBatchResults.length || 0),
                    Number(lastBatchStatusPayload.success || 0),
                    Number(lastBatchStatusPayload.failed || 0),
                );
                if (lastBatchStatusPayload.face_cluster_progress) {
                    updateFaceClusterProgressUI(lastBatchStatusPayload.face_cluster_progress);
                }
            }
            if (currentBatchResults.length > 0) {
                emptyState.classList.add('hidden');
                showBatchOverview();
            }
            return snapshot;
        } catch (error) {
            console.info('batch view snapshot restore skipped', error?.name || 'storage_error');
            return null;
        }
    }

    let batchViewRefreshInFlight = null;

    async function refreshBatchViewAfterReturn() {
        const snapshot = restoreBatchViewSnapshot();
        const sessionId = snapshot?.sessionId || window._currentSessionId;
        if (!sessionId || !snapshot?.active || currentBatchCancelRequested) return;
        if (batchViewRefreshInFlight) return batchViewRefreshInFlight;
        batchViewRefreshInFlight = (async () => {
            try {
                const response = await fetch(`/batch_sessions/${encodeURIComponent(sessionId)}/status`);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || '讀取整理進度失敗');
                applyDriveStatusPayload(data);
                if (data.status === 'completed' && currentBatchResults.length > 0) showBatchOverview();
            } catch (error) {
                console.info('batch view refresh deferred', error?.message || 'status_unavailable');
            } finally {
                batchViewRefreshInFlight = null;
            }
        })();
        return batchViewRefreshInFlight;
    }

    window.addEventListener('pageshow', refreshBatchViewAfterReturn);
    window.addEventListener('focus', refreshBatchViewAfterReturn);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refreshBatchViewAfterReturn();
    });

    async function pollDriveBatchStatus(sessionId, signal) {
        let connectionFailures = 0;
        while (true) {
            if (signal?.aborted || currentBatchCancelRequested) {
                throw new DOMException('Aborted', 'AbortError');
            }
            let data;
            try {
                const response = await fetch(`/batch_sessions/${encodeURIComponent(sessionId)}/status`, { signal });
                data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.detail || data.error_message || '讀取整理進度失敗');
                }
                connectionFailures = 0;
            } catch (error) {
                if (error.name === 'AbortError') throw error;
                connectionFailures += 1;
                if (connectionFailures >= 2) {
                    setLoadingMessage('連線不太穩，正在重新確認進度…', '畫面會繼續等服務回報，不會把已完成的結果弄丟。');
                }
                await wait(Math.min(DRIVE_BATCH_POLL_INTERVAL_MS * connectionFailures, 6000));
                continue;
            }

            applyDriveStatusPayload(data);
            if (data.status === 'completed') return data;
            if (data.status === 'cancelled') {
                throw new Error(data.message || '已中止本次整理');
            }
            if (data.status === 'failed') {
                throw new Error(data.error_message || '雲端照片整理沒有完成');
            }
            await wait(DRIVE_BATCH_POLL_INTERVAL_MS);
        }
    }

    // === Batch Mode Handling ===
    analyzeBatchBtn.addEventListener('click', async () => {
        const source = document.querySelector('input[name="batch-source"]:checked').value;
        batchMode = source === 'local' ? 'upload' : source;
        const currentConcurrency = parseInt(batchConcurrency.value) || 3;
        const runPublicClassification = batchRunPublic ? batchRunPublic.checked : false;
        const runFaceClustering = batchRunFaces ? batchRunFaces.checked : true;
        let faceClusterParams = getFaceClusterDefaults();

        try {
            validateBatchConcurrency(source, currentConcurrency);
        } catch (error) {
            showToast(error.message, 'error');
            return;
        }

        if (!runPublicClassification && !runFaceClustering) {
            showToast('至少選擇一項要執行的功能', 'error');
            return;
        }

        if (runFaceClustering) {
            try {
                faceClusterParams = readFaceClusterParams();
            } catch (error) {
                showToast(error.message, 'error');
                return;
            }
        }

        // Generate session_id for metrics tracking
        const sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        window._currentSessionId = sessionId;
        sessionStorage.removeItem(BATCH_VIEW_SNAPSHOT_KEY);

        let endpoint = '/batch_upload_stream/';
        let body = {};
        let requestOptions;
        let localUploadChunks = [];
        let localUploadCommonFields = null;

        if (source === 'local') {
            const validationError = validateBatchFiles(batchSelectedFiles, 'local');
            if (batchSelectedFiles.length === 0 || validationError) {
                showToast(validationError || '請先選擇這場活動的照片', 'error');
                return;
            }
            localUploadChunks = buildLocalUploadChunks(batchSelectedFiles);
            localUploadCommonFields = {
                concurrency: String(currentConcurrency),
                color_rules_json: JSON.stringify(colorSwatches),
                session_id: sessionId,
                face_cluster_eps: String(faceClusterParams.eps),
                face_cluster_min_samples: String(faceClusterParams.minSamples),
                run_public_classification: String(runPublicClassification),
                run_face_clustering: String(runFaceClustering),
                upload_chunk_total: String(localUploadChunks.length),
                upload_total_files: String(batchSelectedFiles.length),
            };
            const memory = window._collaborativeMemories?.local;
            if (memory) localUploadCommonFields.collaborative_memory = memory;
            requestOptions = { method: 'POST' };
        } else {
            const fId = driveFolderId.value.trim();
            const tId = driveTargetId.value.trim();
            if (!fId) {
                showToast('請先選一個 Google 雲端資料夾', 'error');
                return;
            }
            endpoint = '/batch_drive_start/';
            body = {
                folder_id: fId,
                target_folder_id: tId || null,
                concurrency: currentConcurrency,
                color_rules: colorSwatches,
                session_id: sessionId,
                face_cluster_eps: faceClusterParams.eps,
                face_cluster_min_samples: faceClusterParams.minSamples,
                run_public_classification: runPublicClassification,
                run_face_clustering: runFaceClustering,
                collaborative_memory: window._collaborativeMemories?.drive || null,
            };
            requestOptions = {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            };
        }
        const busyLabel = runPublicClassification && runFaceClustering
            ? '判定並整理人物'
            : runPublicClassification ? '判定照片' : '整理人物';
        if (!(await beginSharedBusy(busyLabel))) return;
        currentBatchAbortController = new AbortController();
        currentBatchCancelRequested = false;
        currentFaceClusterJobId = null;
        requestOptions.signal = currentBatchAbortController.signal;

        // Reset and Show Progress UI
        updateProgressUI(0, 0, 0, 0);
        currentBatchResults = [];
        currentFaceClusters = [];
        selectedFaceClusterId = null;
        faceClusteringInfo = null;
        batchFailureDetails = [];
        faceClusterUi.expanded.clear();
        faceClusterUi.selectedEvidenceIndexes.clear();
        relationshipViewMode = 'people';
        photoPeopleAssignments = {};
        photoClusterUi.expanded.clear();
        batchOverviewActive = false;
        window._currentMetrics = null;
        window._currentSessionId = sessionId;
        saveBatchViewSnapshot({ active: true });
        document.getElementById('batch-overview').classList.add('hidden');
        document.getElementById('back-to-overview-btn').classList.add('hidden');
        document.getElementById('batch-metrics-summary').classList.add('hidden');
        reviewSummary.style.display = 'none';
        decisionButtons.style.display = 'none';
        splitViewer.classList.add('hidden');
        emptyState.classList.remove('hidden');
        showLoading(true);
        setLoadingCancelButton({ visible: true });
        setLoadingMessage(
            runPublicClassification && runFaceClustering
                ? '正在判定照片並整理人物…'
                : runPublicClassification ? '正在判定照片是否適合分享…' : '正在整理照片中的人物…',
            runFaceClustering
                ? '先一張一張看過照片，接著會回報人物分群進度。'
                : '照片會一張一張回來，完成後就能查看結果。'
        );

        try {
            if (source !== 'local') {
                const response = await fetch(endpoint, requestOptions);
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    logBatchRequestFailure({
                        source,
                        stage: 'batch_start',
                        endpoint,
                        sessionId,
                        status: response.status,
                        payload: data,
                    });
                    throw new Error(data.detail || data.error_message || '沒能開始，再試一次好嗎');
                }
                applyDriveStatusPayload(data);
                const finalData = await pollDriveBatchStatus(sessionId, currentBatchAbortController.signal);
                applyDriveStatusPayload(finalData);
                saveBatchViewSnapshot({ active: false });
                showToast(`看完了。${Number(finalData.success || 0)} 張看過${Number(finalData.failed || 0) ? `，${Number(finalData.failed || 0)} 張沒看成` : ''}`);
                flushBatchFailureSummary();
                organizeArea.classList.add('hidden');
                if (currentBatchResults.length > 0) {
                    showBatchOverview();
                }
                return;
            }

            let successCount = 0;
            let failedCount = 0;
            let totalImages = batchSelectedFiles.length;

            for (let chunkIndex = 0; chunkIndex < localUploadChunks.length; chunkIndex++) {
                if (currentBatchAbortController.signal.aborted || currentBatchCancelRequested) {
                    throw new DOMException('Aborted', 'AbortError');
                }
                const formData = new FormData();
                localUploadChunks[chunkIndex].forEach(file => formData.append('files', file, file.name));
                Object.entries(localUploadCommonFields || {}).forEach(([key, value]) => {
                    formData.append(key, value);
                });
                formData.append('upload_chunk_index', String(chunkIndex));
                setLoadingMessage(
                    runPublicClassification && runFaceClustering
                        ? '正在判定照片並整理人物…'
                        : runPublicClassification ? '正在判定照片是否適合分享…' : '正在整理照片中的人物…',
                    `正在上傳第 ${chunkIndex + 1} / ${localUploadChunks.length} 批，已完成 ${successCount + failedCount} / ${totalImages} 張。`
                );

                const response = await fetch(endpoint, {
                    ...requestOptions,
                    body: formData,
                    signal: currentBatchAbortController.signal,
                });

                if (!response.ok) {
                    const err = await response.json();
                    logBatchRequestFailure({
                        source,
                        stage: 'batch_start',
                        endpoint,
                        sessionId,
                        status: response.status,
                        payload: err,
                    });
                    throw new Error(err.detail || '沒能開始，再試一次好嗎');
                }

                // 處理串流結果
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    // 將二進位數據轉為文字
                    buffer += decoder.decode(value, { stream: true });

                    // NDJSON 處理：根據換行符號切割每一行完整的 JSON
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // 未完成的行留到下次處理

                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const data = JSON.parse(line);

                            // 進度與結果處理
                            if (data.status === 'face_cluster_progress') {
                                if (data.job_id) currentFaceClusterJobId = data.job_id;
                                updateFaceClusterProgressUI(data);
                                continue;
                            }

                            if (data.status === 'ok') {
                                // Drive 串流模式：每行一筆 NDJSON
                                successCount++;
                                totalImages = data.total;
                                const result = data.result || data;
                                if (publicClassificationWasRun(data)) {
                                    const aiStatus = result.moderation_status || (result.is_safe_for_public ? 'public' : 'private');
                                    if (!result.ai_decision) {
                                        result.ai_decision = aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending';
                                    }
                                    data.user_decision = data.user_decision || result.ai_decision;
                                    data.ai_decision = result.ai_decision;
                                } else {
                                    data.public_classification_performed = false;
                                }
                                // 保持 session_id
                                if (data.session_id) window._currentSessionId = data.session_id;
                                currentBatchResults.push(data);
                            } else if (data.status === 'error') {
                                failedCount++;
                                totalImages = data.total || totalImages;
                                recordBatchFailure(data);
                            } else if (data.status === 'completed') {
                                setCurrentFaceClusters(data.face_clusters);
                                faceClusteringInfo = data.face_clustering || null;
                            } else if (data.status === 'cancelled') {
                                throw new Error(data.message || '已中止本次整理');
                            } else if (data.results && Array.isArray(data.results)) {
                                // 本機批次模式：一次性完整 JSON 回應
                                totalImages = data.total || data.results.length;
                                if (data.temp_folder) currentTempFolder = data.temp_folder;
                                if (data.session_id) window._currentSessionId = data.session_id;
                                data.results.forEach(item => {
                                    if (item.status === 'ok') {
                                        if (publicClassificationWasRun(item)) {
                                            const aiStatus = item.moderation_status || (item.is_safe_for_public ? 'public' : 'private');
                                            item.user_decision = item.user_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                                            item.ai_decision = item.ai_decision || item.user_decision;
                                        }
                                        currentBatchResults.push(item);
                                        successCount++;
                                    } else {
                                        failedCount++;
                                        recordBatchFailure(item);
                                    }
                                });
                                setCurrentFaceClusters(data.face_clusters);
                                faceClusteringInfo = data.face_clustering || null;
                            }

                            // 更新 UI 進度
                            updateProgressUI(successCount + failedCount, totalImages, successCount, failedCount);
                            saveBatchViewSnapshot({ active: true });

                        } catch (err) {
                            console.error('JSON parsing data error:', line, err);
                        }
                    }
                }

                // 一次性 JSON（本機資料夾模式）通常不以換行結尾，需處理最後的 buffer。
                if (buffer.trim()) {
                    const data = JSON.parse(buffer);
                    if (data.results && Array.isArray(data.results)) {
                        totalImages = data.total || data.results.length;
                        if (data.temp_folder) currentTempFolder = data.temp_folder;
                        if (data.session_id) window._currentSessionId = data.session_id;
                        data.results.forEach(item => {
                            if (item.status === 'ok') {
                                if (publicClassificationWasRun(item)) {
                                    const aiStatus = item.moderation_status || (item.is_safe_for_public ? 'public' : 'private');
                                    item.user_decision = item.user_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                                    item.ai_decision = item.ai_decision || item.user_decision;
                                }
                                currentBatchResults.push(item);
                                successCount++;
                            } else {
                                failedCount++;
                                recordBatchFailure(item);
                            }
                        });
                        setCurrentFaceClusters(data.face_clusters);
                        faceClusteringInfo = data.face_clustering || null;
                        updateProgressUI(successCount + failedCount, totalImages, successCount, failedCount);
                    }
                }
            }

            showToast(`看完了。${successCount} 張看過${failedCount ? `，${failedCount} 張沒看成` : ''}`);
            flushBatchFailureSummary();

            if (source === 'local') {
                organizeArea.classList.add('hidden');
            } else {
                organizeArea.classList.add('hidden');
            }

            if (currentBatchResults.length > 0) {
                showBatchOverview();
                // metrics 等用戶確認後再顯示
            }
            saveBatchViewSnapshot({ active: false });

        } catch (e) {
            logBatchRequestFailure({
                source,
                stage: 'batch_runtime',
                endpoint,
                sessionId,
                error: e?.message || String(e),
                cancelRequested: currentBatchCancelRequested,
            });
            if (e.name === 'AbortError' || currentBatchCancelRequested) {
                showToast('已中止本次整理');
            } else {
                showToast(e.message, 'error');
            }
        } finally {
            showLoading(false);
            setLoadingCancelButton({ visible: false, disabled: false });
            currentBatchAbortController = null;
            currentBatchCancelRequested = false;
            currentFaceClusterJobId = null;
            endSharedBusy();
            setLoadingMessage('正在一張一張看過去…', '照片會一張一張回來，完成後再整理人物。');
        }
    });

    loadingCancelBtn?.addEventListener('click', () => {
        cancelCurrentBatchOperation().catch(error => {
            console.error('cancel batch failed', error);
            showToast('中止請求沒有送成功', 'error');
        });
    });

    function updatePageIndicator() {
        if (currentBatchResults.length > 0) {
            pageIndicator.textContent = `${currentIndex + 1} / ${currentBatchResults.length}`;
        }
    }

    function renderBatchViewer() {
        if (currentBatchResults.length === 0) return;

        const currentData = currentBatchResults[currentIndex];

        emptyState.classList.add('hidden');
        splitViewer.classList.remove('hidden');

        pageIndicator.textContent = `${currentIndex + 1} / ${currentBatchResults.length}`;

        // 更新裁決按鈕狀態
        renderDecisionButtons();

        // 判斷是否為雲端模式或本地模式的串流數據
        const isStream = !!currentData.drawn_image_b64;

        if (isStream) {
            // Streaming / Drive Mode
            if (currentData.original_image_b64) {
                originalImg.src = 'data:image/jpeg;base64,' + currentData.original_image_b64;
            } else {
                originalImg.src = 'https://placehold.co/600x400?text=Processing+Drive+File';
            }
            annotatedImg.src = 'data:image/jpeg;base64,' + currentData.drawn_image_b64;
            renderFaceFocusOverlay();

            // 輔助 UI: 更新統計數據
            const analysis = currentData.result;
            updateStatsUI(currentData.file_name, analysis);
        } else {
            // Local File Mode (Non-Stream)
            originalImg.src = `/local_file/?path=${encodeURIComponent(currentData.original_path)}`;
            annotatedImg.src = `/local_file/?path=${encodeURIComponent(currentData.output)}`;
            renderFaceFocusOverlay();

            const fakeAnalysis = {
                moderation_status: currentData.moderation_status,
                is_safe_for_public: currentData.is_safe_for_public,
                moderation_reason: currentData.moderation_reason,
                face_bboxes: new Array(currentData.face_count),
                strap_bboxes: currentData.has_brand_strap ? [1] : [],
                strap_color: currentData.strap_color
            };
            updateStatsUI(currentData.file, fakeAnalysis);
        }

        // 用 user_decision 覆寫 status badge（確保顯示的是用戶目前有效決定）
        const ud = currentData.user_decision;
        if (ud === 'safe') {
            safetyBadge.textContent = '可以分享';
            safetyBadge.className = 'status-badge status-safe';
        } else if (ud === 'pending') {
            safetyBadge.textContent = '之後再看';
            safetyBadge.className = 'status-badge status-pending';
        } else if (ud === 'unsafe') {
            safetyBadge.textContent = '先留著';
            safetyBadge.className = 'status-badge status-unsafe';
        }

        updateOverrideIndicator(currentData);
    }

    // === Batch Overview ===

    function showBatchOverview() {
        batchOverviewActive = true;
        emptyState.classList.add('hidden');
        splitViewer.classList.add('hidden');
        reviewSummary.style.display = 'none';
        decisionButtons.style.display = 'none';
        document.getElementById('back-to-overview-btn').classList.add('hidden');
        document.getElementById('batch-overview').classList.remove('hidden');
        renderBatchOverview();
    }

    function renderBatchOverview() {
        let safeC = 0, unsafeC = 0, pendingC = 0;
        currentBatchResults.forEach(r => {
            if (r.user_decision === 'safe') safeC++;
            else if (r.user_decision === 'pending' || !publicClassificationWasRun(r)) pendingC++;
            else unsafeC++;
        });
        document.getElementById('ov-total').textContent = currentBatchResults.length;
        document.getElementById('ov-safe').textContent = safeC;
        document.getElementById('ov-unsafe').textContent = unsafeC;
        document.getElementById('ov-pending').textContent = pendingC;
        saveDriveResultsBtn?.classList.toggle('hidden', batchMode !== 'drive');

        const content = document.getElementById('overview-content');
        const viewToggle = document.querySelector('.view-mode-toggle');
        if (currentFaceClusters.length > 0) {
            viewToggle?.classList.add('hidden');
            renderFaceWorkspace(content);
        } else if (faceClusteringInfo && faceClusteringInfo.available === false) {
            viewToggle?.classList.remove('hidden');
            content.innerHTML = `<div class="face-workspace-notice">
                <strong>${faceClusteringInfo.reason === 'not_requested' ? '本次未執行人物分群' : '人物分群目前未啟用'}</strong>
                <span>${escapeHtml(faceClusteringInfo.message || '目前先顯示照片審核結果。')}</span>
            </div>`;
            const photoContent = document.createElement('div');
            content.appendChild(photoContent);
            if (batchOverviewMode === 'thumbnail') renderThumbnailGrid(photoContent);
            else renderOverviewList(photoContent);
        } else if (batchOverviewMode === 'thumbnail') {
            viewToggle?.classList.remove('hidden');
            renderThumbnailGrid(content);
        } else {
            viewToggle?.classList.remove('hidden');
            renderOverviewList(content);
        }
    }

    function statusLabel(status) {
        return ({ unconfirmed: '未命名', pending: '待確認', confirmed: '已確認', merged: '已合併' })[status] || '待確認';
    }

    function findResultIndex(fileName) {
        return currentBatchResults.findIndex(item => (item.file_name || item.file) === fileName);
    }

    function getFaceEvidenceDecision(evidence) {
        const resultIndex = findResultIndex(evidence.file_name);
        const item = resultIndex >= 0 ? currentBatchResults[resultIndex] : null;
        const wasRun = publicClassificationWasRun(item);
        const decision = item?.user_decision || item?.ai_decision || 'pending';
        return {
            resultIndex,
            decision,
            label: !wasRun && !item?.user_decision
                ? '未判定'
                : decision === 'safe' ? '可公開' : decision === 'unsafe' ? '不可公開' : '待確認',
        };
    }

    function renderFacePhotoDecision(evidence, clusterId = '', evidenceIndex = -1) {
        if (!evidence) {
            return '<p class="face-photo-decision-hint">點選任一人臉框，查看該張照片的可公開判定。</p>';
        }
        const decision = getFaceEvidenceDecision(evidence);
        const personOptions = currentFaceClusters
            .filter(cluster => String(cluster.cluster_id) !== String(clusterId))
            .map(cluster => `<option value="${escapeHtml(cluster.cluster_id)}">${escapeHtml(cluster.display_name || cluster.cluster_id)}</option>`)
            .join('');
        return `<div class="face-photo-decision status-${escapeHtml(decision.decision)}" role="status">
            <span>這張照片的判定</span>
            <strong>${decision.label}</strong>
            <button type="button" data-face-action="open-photo" data-cluster-id="${escapeHtml(clusterId)}" data-result-index="${decision.resultIndex}" data-evidence-index="${evidenceIndex}">查看完整照片</button>
        </div>
        ${personOptions ? `<div class="face-evidence-transfer" data-face-transfer-source="${escapeHtml(clusterId)}" data-evidence-index="${evidenceIndex}">
            <label>更換所屬人物
                <select class="face-transfer-target">
                    <option value="">選擇人物</option>
                    ${personOptions}
                </select>
            </label>
            <button type="button" data-face-action="transfer-evidence" data-cluster-id="${escapeHtml(clusterId)}" data-evidence-index="${evidenceIndex}">移到此人物</button>
        </div>` : ''}`;
    }

    function cropFaceEvidenceImages(container) {
        container.querySelectorAll('img[data-face-bbox]').forEach(img => {
            const bbox = String(img.dataset.faceBbox || '').split(',').map(Number);
            if (bbox.length !== 4 || bbox.some(value => !Number.isFinite(value))) return;
            const source = img.currentSrc || img.src;
            const sourceImage = new Image();
            sourceImage.onload = () => {
                const [x1, y1, x2, y2] = bbox;
                const left = Math.max(0, Math.min(sourceImage.naturalWidth, Math.floor(x1)));
                const top = Math.max(0, Math.min(sourceImage.naturalHeight, Math.floor(y1)));
                const right = Math.max(left + 1, Math.min(sourceImage.naturalWidth, Math.ceil(x2)));
                const bottom = Math.max(top + 1, Math.min(sourceImage.naturalHeight, Math.ceil(y2)));
                const canvas = document.createElement('canvas');
                canvas.width = right - left;
                canvas.height = bottom - top;
                canvas.getContext('2d').drawImage(
                    sourceImage,
                    left,
                    top,
                    canvas.width,
                    canvas.height,
                    0,
                    0,
                    canvas.width,
                    canvas.height,
                );
                img.removeAttribute('data-face-bbox');
                img.src = canvas.toDataURL('image/jpeg', 0.88);
            };
            sourceImage.src = source;
        });
    }

    function renderClusterLeadPreview(cluster) {
        const leadEvidence = Array.isArray(cluster?.evidence_photos) ? cluster.evidence_photos[0] : null;
        if (!leadEvidence) {
            return `<span class="face-cluster-avatar">${escapeHtml(String(cluster?.display_name || '人').trim().charAt(0) || '人')}</span>`;
        }
        const decision = getFaceEvidenceDecision(leadEvidence);
        const source = leadEvidence.image_b64
            ? `data:image/jpeg;base64,${leadEvidence.image_b64}`
            : (decision.resultIndex >= 0 ? getItemImgSrc(currentBatchResults[decision.resultIndex]) : '');
        const bbox = Array.isArray(leadEvidence.bbox) ? leadEvidence.bbox.map(Number).join(',') : '';
        if (!source) {
            return `<span class="face-cluster-avatar">${escapeHtml(String(cluster?.display_name || '人').trim().charAt(0) || '人')}</span>`;
        }
        return `<span class="face-cluster-avatar face-cluster-avatar-photo">
            <img src="${source}" data-face-bbox="${escapeHtml(bbox)}" alt="${escapeHtml(cluster.display_name || '人物')} 的代表截圖" loading="lazy">
        </span>`;
    }

    function renderPhotoPersonChip(cluster) {
        return `<span class="photo-person-chip" title="${escapeHtml(cluster.display_name || cluster.cluster_id)}">
            ${renderClusterLeadPreview(cluster)}
            <span class="photo-person-chip-name">${escapeHtml(cluster.display_name || cluster.cluster_id)}</span>
        </span>`;
    }

    function recalculateFaceClusterCounts(cluster) {
        const evidence = Array.isArray(cluster?.evidence_photos) ? cluster.evidence_photos : [];
        const photoNames = new Set(evidence.map(item => String(item.file_name || '')).filter(Boolean));
        cluster.face_count = evidence.length;
        cluster.photo_count = photoNames.size;
    }

    function removeClusterFromPhotoAssignments(clusterId) {
        Object.keys(photoPeopleAssignments).forEach(fileName => {
            photoPeopleAssignments[fileName] = (photoPeopleAssignments[fileName] || [])
                .filter(item => String(item) !== String(clusterId));
        });
    }

    function moveEvidenceToCluster(sourceClusterId, evidenceIndex, targetClusterId) {
        const sourceCluster = currentFaceClusters.find(cluster => String(cluster.cluster_id) === String(sourceClusterId));
        const targetCluster = currentFaceClusters.find(cluster => String(cluster.cluster_id) === String(targetClusterId));
        if (!sourceCluster || !targetCluster || sourceCluster === targetCluster) return false;
        const sourceEvidence = Array.isArray(sourceCluster.evidence_photos) ? sourceCluster.evidence_photos : [];
        const movedEvidence = sourceEvidence[evidenceIndex];
        if (!movedEvidence) return false;

        sourceEvidence.splice(evidenceIndex, 1);
        sourceCluster.evidence_photos = sourceEvidence;
        targetCluster.evidence_photos = Array.isArray(targetCluster.evidence_photos) ? targetCluster.evidence_photos : [];
        targetCluster.evidence_photos.push(movedEvidence);
        recalculateFaceClusterCounts(sourceCluster);
        recalculateFaceClusterCounts(targetCluster);

        const fileName = String(movedEvidence.file_name || '');
        if (fileName) {
            const assigned = new Set((photoPeopleAssignments[fileName] || []).map(String));
            assigned.add(String(targetClusterId));
            const sourceStillInPhoto = sourceEvidence.some(item => String(item.file_name || '') === fileName);
            if (!sourceStillInPhoto) assigned.delete(String(sourceClusterId));
            photoPeopleAssignments[fileName] = Array.from(assigned);
        }

        faceClusterUi.selectedEvidenceIndexes.delete(String(sourceClusterId));
        faceClusterUi.expanded.add(String(targetClusterId));
        selectedFaceClusterId = String(targetClusterId);
        return true;
    }

    function deleteFaceCluster(clusterId) {
        const cluster = currentFaceClusters.find(item => String(item.cluster_id) === String(clusterId));
        if (!cluster) return;
        const confirmed = window.confirm(`確定要刪除「${cluster.display_name || cluster.cluster_id}」嗎？\n\n此人物會從本次辨識結果與輸出人物資料夾移除，原始照片不會被刪除。`);
        if (!confirmed) return;
        currentFaceClusters = currentFaceClusters.filter(item => String(item.cluster_id) !== String(clusterId));
        removeClusterFromPhotoAssignments(clusterId);
        faceClusterUi.expanded.delete(String(clusterId));
        faceClusterUi.selectedEvidenceIndexes.delete(String(clusterId));
        if (String(selectedFaceClusterId) === String(clusterId)) selectedFaceClusterId = currentFaceClusters[0]?.cluster_id || null;
        renderBatchOverview();
        showToast(`已從本次結果移除「${cluster.display_name || cluster.cluster_id}」`);
    }

    function renderPeoplePerspective(container) {
        const clusterItems = currentFaceClusters.map(cluster => {
            const clusterId = String(cluster.cluster_id);
            const isExpanded = faceClusterUi.expanded.has(clusterId);
            const selectedEvidenceIndex = faceClusterUi.selectedEvidenceIndexes.get(clusterId);
            const selectedEvidence = Number.isInteger(selectedEvidenceIndex)
                ? (cluster.evidence_photos || [])[selectedEvidenceIndex]
                : null;
            const evidence = (cluster.evidence_photos || []).map((item, evidenceIndex) => {
                const decision = getFaceEvidenceDecision(item);
                const source = item.image_b64
                    ? `data:image/jpeg;base64,${item.image_b64}`
                    : (decision.resultIndex >= 0 ? getItemImgSrc(currentBatchResults[decision.resultIndex]) : '');
                const bbox = Array.isArray(item.bbox) ? item.bbox.map(Number).join(',') : '';
                const selectedClass = evidenceIndex === selectedEvidenceIndex ? ' selected' : '';
                return `<button class="face-evidence-card${selectedClass}" type="button"
                        data-face-action="select-evidence" data-cluster-id="${escapeHtml(clusterId)}"
                        data-evidence-index="${evidenceIndex}">
                    <span class="face-crop-frame">
                        ${source ? `<img src="${source}" data-face-bbox="${escapeHtml(bbox)}" alt="${escapeHtml(item.file_name)} 的人臉" loading="lazy">` : '<span class="face-evidence-missing">無預覽</span>'}
                    </span>
                    <span>${escapeHtml(item.file_name)}</span>
                </button>`;
            }).join('');
            return `<article class="face-person-card${isExpanded ? ' expanded' : ''}">
                <div class="face-person-row">
                    <button class="face-person-toggle" type="button" data-face-action="toggle-cluster"
                            data-cluster-id="${escapeHtml(clusterId)}" aria-expanded="${isExpanded}" aria-label="${isExpanded ? '收合' : '展開'} ${escapeHtml(cluster.display_name)}">
                        <span class="face-cluster-arrow" aria-hidden="true">${isExpanded ? '▼' : '▶'}</span>
                        ${renderClusterLeadPreview(cluster)}
                        <span class="face-cluster-copy">
                            <strong>${escapeHtml(cluster.display_name || clusterId)}</strong>
                            <small>${cluster.photo_count} 張照片 · ${cluster.face_count} 張人臉框</small>
                        </span>
                    </button>
                    <div class="face-person-name-editor">
                        <input class="face-person-name-input" value="${escapeHtml(cluster.display_name)}" maxlength="80" aria-label="人物名稱">
                        <button type="button" data-face-action="save-name" data-cluster-id="${escapeHtml(clusterId)}">儲存名稱</button>
                        <button class="face-delete-inline-btn" type="button" data-face-action="delete-cluster" data-cluster-id="${escapeHtml(clusterId)}">刪除</button>
                    </div>
                </div>
                ${isExpanded ? `<div class="face-person-body">
                    <section class="face-evidence-panel">
                        <div class="face-panel-heading"><span>辨識為這個人的人臉框</span><small>依最長邊等比例適配</small></div>
                        <div class="face-evidence-grid">${evidence || '<p class="face-empty-copy">沒有可顯示的人臉框</p>'}</div>
                        <div class="face-photo-decision-slot">${renderFacePhotoDecision(selectedEvidence, clusterId, selectedEvidenceIndex)}</div>
                    </section>
                    <aside class="face-decision-panel" data-cluster-editor="${escapeHtml(clusterId)}">
                        <div class="face-panel-heading"><span>確認人物</span><small>${statusLabel(cluster.status)}</small></div>
                        <label>確認狀態<select class="face-cluster-status-input">
                            <option value="unconfirmed" ${cluster.status === 'unconfirmed' ? 'selected' : ''}>未命名</option>
                            <option value="pending" ${cluster.status === 'pending' ? 'selected' : ''}>待確認</option>
                            <option value="confirmed" ${cluster.status === 'confirmed' ? 'selected' : ''}>已確認</option>
                        </select></label>
                        <label>備註<textarea class="face-cluster-notes" rows="3" maxlength="500" placeholder="例如：講師、工作人員">${escapeHtml(cluster.notes || '')}</textarea></label>
                        <button class="face-save-btn" type="button" data-face-action="save-cluster" data-cluster-id="${escapeHtml(clusterId)}">儲存這個人物</button>
                        <small class="face-save-hint">流水 ID：${escapeHtml(clusterId)}</small>
                    </aside>
                </div>` : ''}
            </article>`;
        }).join('');

        container.innerHTML = `<div class="face-cluster-list">${clusterItems}</div>`;

        cropFaceEvidenceImages(container);
    }

    function getResultDecision(item) {
        const analysis = item.result || item;
        const wasRun = publicClassificationWasRun(item);
        const decision = item.user_decision || item.ai_decision || analysis.ai_decision
            || (analysis.moderation_status === 'public' || analysis.is_safe_for_public ? 'safe'
                : analysis.moderation_status === 'pending' ? 'pending' : 'unsafe');
        return {
            value: decision,
            label: !wasRun && !item.user_decision
                ? '未判定'
                : decision === 'safe' ? '可公開' : decision === 'pending' ? '待確認' : '不可公開',
        };
    }

    function renderPhotoPerspective(container) {
        const peopleById = new Map(currentFaceClusters.map(cluster => [String(cluster.cluster_id), cluster]));
        const photoItems = currentBatchResults.map((item, resultIndex) => {
            const fileName = String(item.file_name || item.file || `圖片 ${resultIndex + 1}`);
            const isExpanded = photoClusterUi.expanded.has(resultIndex);
            const decision = getResultDecision(item);
            const assignedPeople = (photoPeopleAssignments[fileName] || [])
                .map(clusterId => peopleById.get(String(clusterId)))
                .filter(Boolean);
            const peopleChips = assignedPeople.map(cluster =>
                renderPhotoPersonChip(cluster)
            ).join('');
            return `<article class="photo-relation-card${isExpanded ? ' expanded' : ''}">
                <button class="photo-relation-toggle" type="button" data-photo-action="toggle-photo"
                        data-result-index="${resultIndex}" aria-expanded="${isExpanded}">
                    <span class="face-cluster-arrow" aria-hidden="true">${isExpanded ? '▼' : '▶'}</span>
                    <span class="photo-relation-name">${escapeHtml(fileName)}</span>
                    <small>${assignedPeople.length} 位人物</small>
                </button>
                ${isExpanded ? `<div class="photo-relation-body">
                    <img src="${getItemImgSrc(item)}" alt="${escapeHtml(fileName)}" loading="lazy">
                    <div class="photo-relation-details">
                        <div class="face-panel-heading"><span>照片判定</span><strong class="status-${escapeHtml(decision.value)}">${decision.label}</strong></div>
                        <div class="photo-people-summary">${peopleChips || '<span class="photo-people-empty">尚未登記人物</span>'}</div>
                        <div class="photo-relation-actions">
                            <button type="button" data-photo-action="edit-people" data-result-index="${resultIndex}">編輯人物</button>
                            <button type="button" data-photo-action="open-photo" data-result-index="${resultIndex}">查看完整照片</button>
                        </div>
                    </div>
                </div>` : ''}
            </article>`;
        }).join('');
        container.innerHTML = `<div class="photo-relation-list">${photoItems || '<p class="face-empty-copy">沒有可顯示的照片</p>'}</div>`;
        cropFaceEvidenceImages(container);
    }

    function renderFaceWorkspace(container) {
        container.innerHTML = `<section class="face-workspace" aria-label="人物與照片關聯結果">
            <div class="face-workspace-toolbar">
                <div class="face-workspace-toolbar-copy"><strong>${relationshipViewMode === 'people' ? '偵測到的人物' : '辨識過的照片'}</strong><span>列表預設收合</span></div>
                <div class="relationship-view-switch" role="group" aria-label="結果檢視角度">
                    <button type="button" data-relationship-view="people" class="${relationshipViewMode === 'people' ? 'active' : ''}">人物角度</button>
                    <button type="button" data-relationship-view="photos" class="${relationshipViewMode === 'photos' ? 'active' : ''}">照片角度</button>
                </div>
            </div>
            <div class="relationship-view-content"></div>
        </section>`;
        const content = container.querySelector('.relationship-view-content');
        if (relationshipViewMode === 'people') renderPeoplePerspective(content);
        else renderPhotoPerspective(content);

        container.onclick = event => {
            const viewTarget = event.target.closest('[data-relationship-view]');
            if (viewTarget) {
                relationshipViewMode = viewTarget.dataset.relationshipView;
                renderFaceWorkspace(container);
                return;
            }
            const actionTarget = event.target.closest('[data-face-action]');
            if (actionTarget && container.contains(actionTarget)) {
                const clusterId = actionTarget.dataset.clusterId;
                if (actionTarget.dataset.faceAction === 'toggle-cluster') {
                    if (faceClusterUi.expanded.has(clusterId)) faceClusterUi.expanded.delete(clusterId);
                    else faceClusterUi.expanded.add(clusterId);
                    renderFaceWorkspace(container);
                } else if (actionTarget.dataset.faceAction === 'select-evidence') {
                    const evidenceIndex = Number(actionTarget.dataset.evidenceIndex);
                    faceClusterUi.selectedEvidenceIndexes.set(clusterId, evidenceIndex);
                    const personCard = actionTarget.closest('.face-person-card');
                    personCard.querySelectorAll('.face-evidence-card.selected').forEach(card => card.classList.remove('selected'));
                    actionTarget.classList.add('selected');
                    const cluster = currentFaceClusters.find(item => String(item.cluster_id) === String(clusterId));
                    const selectedEvidence = cluster?.evidence_photos?.[evidenceIndex];
                    personCard.querySelector('.face-photo-decision-slot').innerHTML = renderFacePhotoDecision(selectedEvidence, clusterId, evidenceIndex);
                } else if (actionTarget.dataset.faceAction === 'open-photo') {
                    const resultIndex = Number(actionTarget.dataset.resultIndex);
                    const evidenceIndex = Number(actionTarget.dataset.evidenceIndex);
                    const cluster = currentFaceClusters.find(item => String(item.cluster_id) === String(clusterId));
                    const evidence = Number.isInteger(evidenceIndex) ? cluster?.evidence_photos?.[evidenceIndex] : null;
                    if (resultIndex >= 0) openReviewFromOverview(resultIndex, evidence || null);
                } else if (actionTarget.dataset.faceAction === 'save-name') {
                    const name = actionTarget.closest('.face-person-row').querySelector('.face-person-name-input').value.trim();
                    saveFaceClusterUpdate(clusterId, { display_name: name });
                } else if (actionTarget.dataset.faceAction === 'save-cluster') {
                    saveSelectedFaceCluster(clusterId, actionTarget.closest('[data-cluster-editor]'));
                } else if (actionTarget.dataset.faceAction === 'delete-cluster') {
                    deleteFaceCluster(clusterId);
                } else if (actionTarget.dataset.faceAction === 'transfer-evidence') {
                    const transferPanel = actionTarget.closest('.face-evidence-transfer');
                    const targetClusterId = transferPanel?.querySelector('.face-transfer-target')?.value;
                    const evidenceIndex = Number(actionTarget.dataset.evidenceIndex);
                    if (!targetClusterId) {
                        showToast('請先選擇要移到哪一位人物', 'error');
                        return;
                    }
                    if (moveEvidenceToCluster(clusterId, evidenceIndex, targetClusterId)) {
                        renderBatchOverview();
                        const targetCluster = currentFaceClusters.find(item => String(item.cluster_id) === String(targetClusterId));
                        showToast(`已移到「${targetCluster?.display_name || targetClusterId}」`);
                    }
                }
                return;
            }
            const photoTarget = event.target.closest('[data-photo-action]');
            if (!photoTarget || !container.contains(photoTarget)) return;
            const resultIndex = Number(photoTarget.dataset.resultIndex);
            if (photoTarget.dataset.photoAction === 'toggle-photo') {
                if (photoClusterUi.expanded.has(resultIndex)) photoClusterUi.expanded.delete(resultIndex);
                else photoClusterUi.expanded.add(resultIndex);
                renderFaceWorkspace(container);
            } else if (photoTarget.dataset.photoAction === 'edit-people') {
                openPhotoPeopleModal(resultIndex);
            } else if (photoTarget.dataset.photoAction === 'open-photo') {
                openReviewFromOverview(resultIndex);
            }
        };
    }

    async function saveFaceClusterUpdate(clusterId, update) {
        const cluster = currentFaceClusters.find(item => String(item.cluster_id) === String(clusterId));
        if (!cluster) return;
        if ('display_name' in update) update.display_name = update.display_name || cluster.display_name;
        Object.assign(cluster, update);
        renderBatchOverview();
        try {
            const response = await fetch(`/face_clusters/${encodeURIComponent(window._currentSessionId)}/${encodeURIComponent(cluster.cluster_id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(update),
            });
            if (!response.ok) throw new Error('server update failed');
            showToast(`已儲存「${cluster.display_name}」`);
        } catch (error) {
            console.warn('Face cluster update stayed in browser only:', error);
            showToast('名稱已保留在這次下載資料中；伺服器同步失敗', 'error');
        }
    }

    async function saveSelectedFaceCluster(clusterId, editor) {
        const cluster = currentFaceClusters.find(item => String(item.cluster_id) === String(clusterId));
        if (!cluster || !editor) return;
        selectedFaceClusterId = clusterId;
        const update = {
            status: editor.querySelector('.face-cluster-status-input').value,
            notes: editor.querySelector('.face-cluster-notes').value.trim(),
        };
        await saveFaceClusterUpdate(clusterId, update);
    }

    function openPhotoPeopleModal(resultIndex) {
        const item = currentBatchResults[resultIndex];
        if (!item) return;
        const fileName = String(item.file_name || item.file || `圖片 ${resultIndex + 1}`);
        const selectedIds = new Set((photoPeopleAssignments[fileName] || []).map(String));
        const modal = document.getElementById('photo-people-modal');
        modal.dataset.resultIndex = String(resultIndex);
        document.getElementById('photo-people-modal-file').textContent = fileName;
        document.getElementById('photo-people-options').innerHTML = currentFaceClusters.length > 0
            ? currentFaceClusters.map(cluster => {
                const clusterId = String(cluster.cluster_id);
                return `<label class="photo-people-option">
                    <input type="checkbox" value="${escapeHtml(clusterId)}" ${selectedIds.has(clusterId) ? 'checked' : ''}>
                    <span><strong>${escapeHtml(cluster.display_name)}</strong><small>${escapeHtml(clusterId)}</small></span>
                </label>`;
            }).join('')
            : '<p class="photo-people-empty">這一批沒有偵測到人物。</p>';
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        modal.querySelector('input, [data-photo-people-modal-action="save"]')?.focus();
    }

    function closePhotoPeopleModal() {
        const modal = document.getElementById('photo-people-modal');
        if (!modal || modal.classList.contains('hidden')) return;
        modal.classList.add('hidden');
        delete modal.dataset.resultIndex;
        document.body.style.overflow = '';
    }

    document.getElementById('photo-people-modal')?.addEventListener('click', event => {
        const action = event.target.closest('[data-photo-people-modal-action]')?.dataset.photoPeopleModalAction;
        if (action === 'close') {
            closePhotoPeopleModal();
        } else if (action === 'save') {
            const modal = document.getElementById('photo-people-modal');
            const resultIndex = Number(modal.dataset.resultIndex);
            const item = currentBatchResults[resultIndex];
            if (!item) return closePhotoPeopleModal();
            const fileName = String(item.file_name || item.file || `圖片 ${resultIndex + 1}`);
            photoPeopleAssignments[fileName] = Array.from(
                modal.querySelectorAll('#photo-people-options input:checked'),
                input => input.value,
            );
            closePhotoPeopleModal();
            renderBatchOverview();
            showToast(`已更新「${fileName}」的人物關聯`);
        }
    });

    function getItemImgSrc(item) {
        if (item.original_image_b64) {
            return 'data:image/jpeg;base64,' + item.original_image_b64;
        }
        return `/local_file/?path=${encodeURIComponent(item.original_path)}`;
    }

    function escapeHtml(value) {
        return String(value).replace(/[&<>'"]/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        })[character]);
    }

    function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function base64ByteLength(value) {
        if (!value) return 0;
        const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
        return Math.max(0, Math.floor(value.length * 3 / 4) - padding);
    }

    function safeDownloadName(value, fallback) {
        const name = String(value || fallback).split(/[\\/]/).pop();
        return name.replace(/[^a-zA-Z0-9._-]/g, '_') || fallback;
    }

    function safeZipPathSegment(value, fallback) {
        const name = String(value || fallback || '').split(/[\\/]/).pop().trim();
        return name.replace(/[\\/:*?"<>|]/g, '_') || fallback;
    }

    function findSelectedBatchFile(fileName) {
        const targetName = String(fileName || '');
        return batchSelectedFiles.find(file => file.name === targetName)
            || batchSelectedFiles.find(file => safeDownloadName(file.name, file.name) === safeDownloadName(targetName, targetName));
    }

    function buildBatchResultExport() {
        return PhotoRelationships.buildExport({
            sessionId: window._currentSessionId || null,
            batchMode,
            clusters: currentFaceClusters,
            results: currentBatchResults.map((item, index) => ({
                index: index + 1,
                ...item,
                annotated_image_available: Boolean(item.drawn_image_b64),
            })),
            assignments: photoPeopleAssignments,
            exportedAt: new Date().toISOString(),
        });
    }

    function cloneExportData(exportData) {
        if (typeof structuredClone === 'function') return structuredClone(exportData);
        return JSON.parse(JSON.stringify(exportData));
    }

    function addArchiveRelativePaths(exportData, folderPhotoEntries) {
        const archiveByFileName = new Map(
            folderPhotoEntries.map(entry => [entry.photo.file_name, entry.archiveRelativePath]),
        );
        const nextExport = cloneExportData(exportData);
        (nextExport.photos || []).forEach(photo => {
            const archivePath = archiveByFileName.get(photo.file_name);
            if (archivePath) photo.archive_relative_path = archivePath;
        });
        (nextExport.photo_angle_folders || []).forEach(folder => {
            (folder.photos || []).forEach(photo => {
                const archivePath = archiveByFileName.get(photo.file_name);
                if (archivePath) photo.archive_relative_path = archivePath;
            });
        });
        (nextExport.results || []).forEach(result => {
            const archivePath = archiveByFileName.get(result.file_name || result.file);
            if (archivePath) result.archive_relative_path = archivePath;
        });
        return nextExport;
    }

    async function saveExportToDrive(document) {
        const targetFolderId = driveTargetId.value.trim();
        if (batchMode !== 'drive' || !targetFolderId) return { attempted: false };
        try {
            const response = await fetch('/batch_exports/drive', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: window._currentSessionId,
                    target_folder_id: targetFolderId,
                    document,
                }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Google 雲端備份失敗');
            return { attempted: true, success: true, fileName: data.file_name, peopleCopy: data.people_copy };
        } catch (error) {
            return { attempted: true, success: false, error: error.message };
        }
    }

    async function saveBatchResultsToDrive() {
        if (batchMode !== 'drive' || currentBatchResults.length === 0) {
            showToast('目前沒有可儲存的雲端辨識結果', 'error');
            return;
        }

        if (!driveTargetId.value.trim()) {
            handleAuthClick('drive-target-id', saveBatchResultsToDrive);
            return;
        }

        saveDriveResultsBtn.disabled = true;
        showLoading(true);
        document.getElementById('loading-text').textContent = '正在儲存辨識結果…';
        try {
            const result = await saveExportToDrive(buildBatchResultExport());
            if (!result.success) throw new Error(result.error || 'Google 雲端儲存失敗');
            const copiedCount = result.peopleCopy?.copied_count || 0;
            const copySuffix = copiedCount ? `，已複製 ${copiedCount} 張照片到人物資料夾` : '';
            showToast(`已儲存辨識結果：${result.fileName}${copySuffix}`);
        } catch (error) {
            showToast(`儲存失敗：${error.message}`, 'error');
        } finally {
            showLoading(false);
            document.getElementById('loading-text').textContent = '正在一張一張看過去…';
            saveDriveResultsBtn.disabled = false;
        }
    }

    async function downloadBatchResults() {
        if (currentBatchResults.length === 0) {
            showToast('目前沒有可下載的辨識結果', 'error');
            return;
        }

        const exportData = buildBatchResultExport();
        const wantsPhotoFolders = Boolean(includeAnnotatedDownload?.checked);
        const folderPhotoEntries = [];
        (exportData.photo_angle_folders || []).forEach(folder => {
            (folder.photos || []).forEach(photo => {
                const sourceFile = findSelectedBatchFile(photo.file_name);
                if (!sourceFile) return;
                const folderName = safeZipPathSegment(folder.name, `group_${folderPhotoEntries.length + 1}`);
                const photoName = safeZipPathSegment(photo.file_name, sourceFile.name || `image_${folderPhotoEntries.length + 1}.jpg`);
                folderPhotoEntries.push({
                    folderName,
                    photoName,
                    archiveRelativePath: `${folderName}/${photoName}`,
                    photo,
                    sourceFile,
                });
            });
        });
        const exportDocument = wantsPhotoFolders && folderPhotoEntries.length > 0
            ? addArchiveRelativePaths(exportData, folderPhotoEntries)
            : exportData;
        const json = JSON.stringify(exportDocument, null, 2);
        const jsonBlob = new Blob([json], { type: 'application/json;charset=utf-8' });
        const imageBytes = folderPhotoEntries.reduce((total, item) => total + item.sourceFile.size, 0);
        // 本機原圖已由瀏覽器持有，沿用上傳總量限制即可；雲端模式仍保留
        // 下載大小保護，避免一次把大量 Drive 檔案組成瀏覽器 ZIP。
        const maxBytes = batchMode === 'upload'
            ? Infinity
            : (config?.batch_download_max_mb || 8) * 1024 * 1024;
        downloadBatchResultsBtn.disabled = true;
        showLoading(true);
        document.getElementById('loading-text').textContent = '正在整理下載內容…';
        let localMessage = '已下載 JSON 辨識結果';
        let localMessageType = 'success';
        try {
            const shouldUseJsonOnly = !wantsPhotoFolders
                || folderPhotoEntries.length === 0
                || imageBytes + jsonBlob.size > maxBytes
                || typeof JSZip === 'undefined';
            if (shouldUseJsonOnly) {
                downloadBlob(jsonBlob, `photo_people_${Date.now()}.json`);
                if (wantsPhotoFolders) {
                    const reason = typeof JSZip === 'undefined'
                        ? '壓縮元件未載入'
                        : folderPhotoEntries.length === 0
                            ? '找不到可放入照片分類資料夾的本機原圖'
                            : `照片資料夾超過 ${config?.batch_download_max_mb || 8}MB 上限`;
                    localMessage = `${reason}，已自動改下載 JSON`;
                    localMessageType = 'error';
                }
            } else {
                const zip = new JSZip();
                zip.file('result.json', json);
                folderPhotoEntries.forEach(entry => {
                    const folder = zip.folder(entry.folderName);
                    folder.file(entry.photoName, entry.sourceFile);
                });
                const zipBlob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 4 } });
                if (Number.isFinite(maxBytes) && zipBlob.size > maxBytes) {
                    downloadBlob(jsonBlob, `photo_people_${Date.now()}.json`);
                    localMessage = `照片資料夾超過 ${config?.batch_download_max_mb || 8}MB 上限，已自動改下載 JSON`;
                    localMessageType = 'error';
                } else {
                    downloadBlob(zipBlob, `photo_people_${Date.now()}.zip`);
                    localMessage = '已下載 JSON 與照片分類資料夾 ZIP';
                }
            }

            showToast(localMessage, localMessageType);
        } catch (error) {
            console.error('Batch result export failed:', error);
            downloadBlob(jsonBlob, `photo_people_${Date.now()}.json`);
            showToast('人物照片資料夾打包失敗，已自動改下載 JSON', 'error');
        } finally {
            showLoading(false);
            document.getElementById('loading-text').textContent = '正在一張一張看過去…';
            downloadBatchResultsBtn.disabled = false;
        }
    }

    downloadBatchResultsBtn?.addEventListener('click', downloadBatchResults);
    saveDriveResultsBtn?.addEventListener('click', saveBatchResultsToDrive);

    function renderThumbnailGrid(container) {
        let html = '<div class="thumbnail-grid">';
        currentBatchResults.forEach((item, idx) => {
            const decisionInfo = getResultDecision(item);
            const decision = decisionInfo.value;
            const isOverride = Boolean(item.user_decision && item.ai_decision && item.user_decision !== item.ai_decision);
            const fileName = escapeHtml(item.file_name || item.file || `圖片 ${idx + 1}`);
            const src = getItemImgSrc(item);
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = !publicClassificationWasRun(item) && !item.user_decision
                ? '未判定'
                : decision === 'safe' ? '可以分享' : decision === 'pending' ? '之後再看' : '先留著';
            html += `<div class="thumbnail-item" data-idx="${idx}" title="${fileName}">
                <img src="${src}" alt="${fileName}" loading="lazy">
                <div class="thumbnail-overlay">
                    <span class="thumb-badge ${badgeClass}">${badgeText}</span>
                </div>
                ${isOverride ? '<span class="thumb-override">🔄</span>' : ''}
                <div class="thumbnail-name">${fileName}</div>
            </div>`;
        });
        html += '</div>';
        container.innerHTML = html;
        container.querySelectorAll('.thumbnail-item').forEach(el => {
            el.addEventListener('click', () => openReviewFromOverview(parseInt(el.dataset.idx)));
        });
    }

    function renderOverviewList(container) {
        let html = '<div class="overview-list">';
        currentBatchResults.forEach((item, idx) => {
            const decisionInfo = getResultDecision(item);
            const decision = decisionInfo.value;
            const isOverride = Boolean(item.user_decision && item.ai_decision && item.user_decision !== item.ai_decision);
            const fileName = escapeHtml(item.file_name || item.file || `圖片 ${idx + 1}`);
            const src = getItemImgSrc(item);
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = !publicClassificationWasRun(item) && !item.user_decision
                ? '未判定'
                : decision === 'safe' ? '可以分享' : decision === 'pending' ? '之後再看' : '先留著';
            html += `<div class="overview-list-row" data-idx="${idx}">
                <span class="list-row-num">#${idx + 1}</span>
                <img class="list-row-thumb" src="${src}" alt="${fileName}" loading="lazy">
                <span class="list-row-name" title="${fileName}">${fileName}</span>
                ${isOverride ? '<span class="list-row-override">🔄</span>' : ''}
                <span class="list-row-badge ${badgeClass}">${badgeText}</span>
            </div>`;
        });
        html += '</div>';
        container.innerHTML = html;
        container.querySelectorAll('.overview-list-row').forEach(el => {
            el.addEventListener('click', () => openReviewFromOverview(parseInt(el.dataset.idx)));
        });
    }

    function openReviewFromOverview(index, faceEvidence = null) {
        batchOverviewActive = false;
        currentIndex = index;
        focusedFaceEvidence = faceEvidence;
        document.getElementById('batch-overview').classList.add('hidden');
        document.getElementById('batch-metrics-summary').classList.add('hidden');
        emptyState.classList.add('hidden');
        splitViewer.classList.remove('hidden');
        decisionButtons.style.display = 'flex';
        document.getElementById('back-to-overview-btn').classList.remove('hidden');
        renderBatchViewer();
        splitViewer.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    window.__backToOverview = function () {
        batchOverviewActive = true;
        splitViewer.classList.add('hidden');
        const metricsSummary = document.getElementById('batch-metrics-summary');
        if (metricsSummary && !metricsSummary.classList.contains('hidden')) {
            // 已有 metrics，保持顯示
        } else if (window._currentMetrics) {
            metricsSummary.classList.remove('hidden');
        }
        decisionButtons.style.display = 'none';
        document.getElementById('back-to-overview-btn').classList.add('hidden');
        document.getElementById('batch-overview').classList.remove('hidden');
        renderBatchOverview();
    };

    window.__setOverviewMode = function (mode) {
        batchOverviewMode = mode;
        localStorage.setItem('batchOverviewMode', mode);
        renderBatchOverview();
    };

    // Check for auth success in URL
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('auth') === 'success') {
        showToast('雲端連結好了');
        // 自動切換到批次模式
        tabBtns[1].click();
        document.querySelector('input[value="drive"]').checked = true;
        document.querySelector('input[value="drive"]').dispatchEvent(new Event('change'));
        // 同步伺服器端 OAuth token，讓「瀏覽」按鈕不需再次授權
        tryFetchServerToken();
    }

    prevBtn.addEventListener('click', () => {
        if (currentBatchResults.length > 0) {
            currentIndex = (currentIndex - 1 + currentBatchResults.length) % currentBatchResults.length;
            clearFaceFocusOverlay();
            renderBatchViewer();
            highlightCurrentInSummary();
        }
    });

    nextBtn.addEventListener('click', () => {
        if (currentBatchResults.length > 0) {
            currentIndex = (currentIndex + 1) % currentBatchResults.length;
            clearFaceFocusOverlay();
            renderBatchViewer();
            highlightCurrentInSummary();
        }
    });

    // === Human-in-the-Loop Review Functions ===

    function renderDecisionButtons() {
        if (currentBatchResults.length === 0) return;
        const currentData = currentBatchResults[currentIndex];
        if (!currentData) return;

        btnSetSafe.classList.remove('active-safe');
        btnSetPending.classList.remove('active-pending');
        btnSetUnsafe.classList.remove('active-unsafe');

        if (currentData.user_decision === 'safe') {
            btnSetSafe.classList.add('active-safe');
        } else if (currentData.user_decision === 'pending') {
            btnSetPending.classList.add('active-pending');
        } else if (currentData.user_decision === 'unsafe') {
            btnSetUnsafe.classList.add('active-unsafe');
        }
    }

    window.__setDecision = function (decision) {
        if (currentBatchResults.length === 0) return;
        const currentData = currentBatchResults[currentIndex];
        if (!currentData) return;

        currentData.user_decision = decision;
        renderDecisionButtons();

        if (decision === 'safe') {
            safetyBadge.textContent = '可以分享';
            safetyBadge.className = 'status-badge status-safe';
        } else if (decision === 'pending') {
            safetyBadge.textContent = '之後再看';
            safetyBadge.className = 'status-badge status-pending';
        } else {
            safetyBadge.textContent = '先留著';
            safetyBadge.className = 'status-badge status-unsafe';
        }

        updateOverrideIndicator(currentData);

        const toastText = decision === 'safe' ? '可以分享' : decision === 'pending' ? '之後再看' : '先留著';
        showToast(`已經改成「${toastText}」`);
    };

    function renderReviewSummary() {
        if (currentBatchResults.length === 0) return;

        let safeC = 0, unsafeC = 0, pendingC = 0;
        let html = '';

        currentBatchResults.forEach((item, idx) => {
            const decisionInfo = getResultDecision(item);
            const decision = decisionInfo.value;
            const isOverride = Boolean(item.user_decision && item.ai_decision && item.user_decision !== item.ai_decision);
            if (decision === 'safe') safeC++;
            else if (decision === 'pending' || !publicClassificationWasRun(item)) pendingC++;
            else unsafeC++;

            const fileName = item.file_name || item.file || `圖片 ${idx + 1}`;
            const currentClass = idx === currentIndex ? ' current' : '';
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = !publicClassificationWasRun(item) && !item.user_decision
                ? '未判定'
                : decision === 'safe' ? '可以分享' : decision === 'pending' ? '之後再看' : '先留著';

            html += `<div class="review-item${currentClass}" data-idx="${idx}">
                <span class="review-item-index">#${idx + 1}</span>
                <span class="review-item-name" title="${fileName}">${fileName}</span>
                ${isOverride ? '<span class="review-item-override">🔄</span>' : ''}
                <span class="review-item-badge ${badgeClass}">${badgeText}</span>
            </div>`;
        });

        reviewList.innerHTML = html;
        reviewSafeCount.textContent = `可以分享 ${safeC}`;
        reviewPendingCount.textContent = `之後再看 ${pendingC}`;
        reviewUnsafeCount.textContent = `先留著 ${unsafeC}`;

        // 點擊跳轉
        reviewList.querySelectorAll('.review-item').forEach(el => {
            el.addEventListener('click', () => {
                const idx = parseInt(el.dataset.idx);
                currentIndex = idx;
                clearFaceFocusOverlay();
                renderBatchViewer();
                highlightCurrentInSummary();
            });
        });
    }

    function highlightCurrentInSummary() {
        reviewList.querySelectorAll('.review-item').forEach((el, idx) => {
            el.classList.toggle('current', idx === currentIndex);
        });
        // Scroll current item into view
        const currentEl = reviewList.querySelector('.review-item.current');
        if (currentEl) {
            currentEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }

    window.__finalizeReview = async function () {
        if (currentBatchResults.length === 0) {
            showToast('還沒有可以整理的照片', 'error');
            return;
        }

        function setBusy(busy) {
            [document.getElementById('overview-finalize-btn'), finalizeBtn].forEach(btn => {
                if (!btn) return;
                btn.disabled = busy;
                btn.textContent = busy ? '整理中…' : '就這樣，整理好';
            });
        }

        if (batchMode === 'upload') {
            window.__showMetricsSummary();
        } else if (batchMode === 'local') {
            const safe = safeFolder.value.trim();
            const unsafe = unsafeFolder.value.trim();
            const pending = document.getElementById('pending-folder').value.trim();

            // 有填資料夾才歸檔，否則只顯示 metrics
            if (safe && unsafe) {
                const adjusted = currentBatchResults.map(r => ({
                    ...r,
                    moderation_status: r.user_decision,
                    is_safe_for_public: r.user_decision === 'safe'
                }));

                setBusy(true);
                showLoading(true);
                document.getElementById('loading-text').textContent = '正在整理照片…';
                try {
                    const res = await fetch('/organize_batch/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            results: adjusted,
                            safe_folder: safe,
                            unsafe_folder: unsafe,
                            pending_folder: pending || null
                        })
                    });
                    if (!res.ok) throw new Error('沒能整理好');
                    const data = await res.json();
                    showToast(data.message);
                } catch (e) {
                    showToast(e.message, 'error');
                } finally {
                    showLoading(false);
                    document.getElementById('loading-text').textContent = '正在一張一張看過去…';
                    setBusy(false);
                }
            }

            window.__showMetricsSummary();

        } else {
            const targetId = driveTargetId.value.trim();

            // 有填 Drive 資料夾才歸檔，否則只顯示 metrics
            if (targetId) {
                const decisions = currentBatchResults.map(r => ({
                    file_name: r.file_name || r.file,
                    drive_id: r.drive_id || r.result?.drive_id,
                    user_decision: r.user_decision
                }));

                setBusy(true);
                showLoading(true);
                document.getElementById('loading-text').textContent = '正在整理雲端照片…';
                try {
                    const res = await fetch('/finalize_review/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ decisions, target_folder_id: targetId })
                    });
                    if (!res.ok) {
                        const err = await res.json();
                        throw new Error(err.detail || '沒能整理好');
                    }
                    const data = await res.json();
                    showToast(`✅ ${data.message}`);
                } catch (e) {
                    showToast(e.message, 'error');
                } finally {
                    showLoading(false);
                    document.getElementById('loading-text').textContent = '正在一張一張看過去…';
                    setBusy(false);
                }
            }

            window.__showMetricsSummary();
        }
    };

    // === Organize Action ===
    organizeBtn.addEventListener('click', async () => {
        const safe = safeFolder.value.trim();
        const unsafe = unsafeFolder.value.trim();

        if (!safe || !unsafe) {
            showToast('請填寫安全與不安全的分流資料夾路徑', 'error');
            return;
        }

        organizeBtn.disabled = true;
        organizeBtn.textContent = '複製中...';
        showLoading(true);
        document.getElementById('loading-text').textContent = '正在複製並整理照片…';

        try {
            const res = await fetch('/organize_batch/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    results: currentBatchResults,
                    safe_folder: safe,
                    unsafe_folder: unsafe
                })
            });

            if (!res.ok) throw new Error('分類複製失敗');
            const data = await res.json();

            if (data.errors && data.errors.length > 0) {
                console.error(data.errors);
                showToast(`部分失敗，請檢查 Console。成功移動：${data.moved} 個檔案`, 'error');
            } else {
                showToast(data.message);
            }

        } catch (e) {
            showToast(e.message, 'error');
        } finally {
            showLoading(false);
            document.getElementById('loading-text').textContent = '正在一張一張看過去…';
            organizeBtn.disabled = false;
            organizeBtn.textContent = '複製檔案並歸檔';
        }
    });

    // === Google Picker Integration ===
    let pickerApiLoaded = false;
    let oauthToken = null;
    let pickerSelectionCallback = null;
    // Fetch config on load
    async function fetchConfig() {
        try {
            const res = await fetch('/api/config');
            config = await res.json();
            console.log("Config loaded:", !!config.google_client_id);
            syncBatchUploadLimitsHint();
            batchConcurrency.value = String(config.batch_upload_concurrency || 5);
            syncBatchConcurrencyInput();
            applyFaceClusterDefaults();
            if (batchRunFaces && !config.face_clustering_enabled) {
                batchRunFaces.checked = false;
                batchRunFaces.disabled = true;
            }
            syncProcessingScope();
        } catch (e) {
            console.error("Failed to fetch config", e);
        }
    }
    fetchConfig();

    // Callback from GAPI
    window.onPickerApiLoad = () => {
        pickerApiLoaded = true;
    };

    const btnBrowseSource = document.getElementById('btn-browse-source');
    const btnBrowseTarget = document.getElementById('btn-browse-target');
    const btnCreateTarget = document.getElementById('btn-create-target');
    const btnRenameTarget = document.getElementById('btn-rename-target');
    const driveFolderModal = document.getElementById('drive-folder-modal');
    const driveFolderName = document.getElementById('drive-folder-name');
    const driveFolderError = document.getElementById('drive-folder-error');
    const driveFolderSubmit = document.getElementById('drive-folder-submit');

    [btnBrowseSource, btnBrowseTarget].forEach(btn => {
        btn.addEventListener('click', () => {
            const targetInputId = btn.id === 'btn-browse-source' ? 'drive-folder-id' : 'drive-target-id';
            handleAuthClick(targetInputId);
        });
    });

    function syncDriveTargetActions() {
        btnRenameTarget.disabled = !driveTargetId.value.trim();
    }

    function closeDriveFolderModal() {
        if (driveFolderSubmit.disabled) return;
        driveFolderModal.classList.add('hidden');
        driveFolderError.classList.add('hidden');
    }

    function openDriveFolderModal(mode) {
        const isRename = mode === 'rename';
        if (isRename && !driveTargetId.value.trim()) {
            showToast('請先選擇要重新命名的輸出區', 'error');
            return;
        }
        driveFolderModal.dataset.mode = mode;
        document.getElementById('drive-folder-modal-title').textContent = isRename ? '重新命名輸出區' : '建立新資料夾';
        document.getElementById('drive-folder-modal-hint').textContent = isRename
            ? '只會更改目前輸出資料夾的名稱。'
            : driveTargetId.value.trim()
                ? '新資料夾會建立在目前選取的輸出區裡，並自動成為新的輸出區。'
                : '新資料夾會建立在「我的雲端硬碟」，並自動設為輸出區。';
        driveFolderSubmit.textContent = isRename ? '儲存新名稱' : '建立並設為輸出區';
        driveFolderName.value = '';
        driveFolderError.classList.add('hidden');
        driveFolderModal.classList.remove('hidden');
        driveFolderName.focus();
    }

    async function submitDriveFolder() {
        const name = driveFolderName.value.trim();
        if (!name) {
            driveFolderError.textContent = '請輸入資料夾名稱';
            driveFolderError.classList.remove('hidden');
            driveFolderName.focus();
            return;
        }

        const isRename = driveFolderModal.dataset.mode === 'rename';
        const currentFolderId = driveTargetId.value.trim();
        driveFolderSubmit.disabled = true;
        driveFolderName.disabled = true;
        driveFolderError.classList.add('hidden');
        driveFolderSubmit.textContent = isRename ? '正在重新命名…' : '正在建立…';
        try {
            const response = await fetch(
                isRename ? `/drive/output-folders/${encodeURIComponent(currentFolderId)}` : '/drive/output-folders',
                {
                    method: isRename ? 'PATCH' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(isRename
                        ? { name }
                        : { name, parent_folder_id: currentFolderId || null }),
                },
            );
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || '資料夾操作失敗');
            driveTargetId.value = data.folder.id;
            driveTargetId.dispatchEvent(new Event('change'));
            driveFolderModal.classList.add('hidden');
            showToast(isRename ? `輸出區已改名為「${data.folder.name}」` : `已建立輸出區「${data.folder.name}」`);
        } catch (error) {
            driveFolderError.textContent = error.message;
            driveFolderError.classList.remove('hidden');
        } finally {
            driveFolderSubmit.disabled = false;
            driveFolderName.disabled = false;
            driveFolderSubmit.textContent = isRename ? '儲存新名稱' : '建立並設為輸出區';
        }
    }

    btnCreateTarget.addEventListener('click', () => openDriveFolderModal('create'));
    btnRenameTarget.addEventListener('click', () => openDriveFolderModal('rename'));
    driveTargetId.addEventListener('input', syncDriveTargetActions);
    driveFolderModal.addEventListener('click', event => {
        const action = event.target.closest('[data-drive-folder-action]')?.dataset.driveFolderAction;
        if (action === 'close') closeDriveFolderModal();
        if (action === 'submit') submitDriveFolder();
    });
    driveFolderName.addEventListener('keydown', event => {
        if (event.key === 'Enter') submitDriveFolder();
        if (event.key === 'Escape') closeDriveFolderModal();
    });
    syncDriveTargetActions();

    function handleAuthClick(targetId, onPicked = null) {
        if (!config || !config.google_client_id) {
            showToast('伺服器未設定 Google Client ID', 'error');
            return;
        }
        if (!config.google_app_id) {
            showToast('伺服器未設定 Google Project Number', 'error');
            return;
        }

        pickerSelectionCallback = onPicked;

        // 若已有 token（由伺服器端 OAuth 同步或先前 Picker 授權取得），直接開啟 Picker
        if (oauthToken) {
            createPicker(targetId);
            return;
        }

        const tokenClient = google.accounts.oauth2.initTokenClient({
            client_id: config.google_client_id,
            scope: 'https://www.googleapis.com/auth/drive.file',  // 統一使用 drive.file scope
            callback: async (response) => {
                if (response.error !== undefined) {
                    throw (response);
                }
                oauthToken = response.access_token;
                createPicker(targetId);
            },
        });

        tokenClient.requestAccessToken({ prompt: 'consent' });
    }

    async function tryFetchServerToken() {
        try {
            const r = await fetch('/auth/access_token');
            if (r.ok) {
                const data = await r.json();
                if (data.access_token) oauthToken = data.access_token;
            }
        } catch (e) { /* 未登入時靜默忽略 */ }
    }
    function createPicker(targetId) {
        if (!pickerApiLoaded) {
            console.warn('Picker API 尚未載入，嘗試重新載入...');
            if (!window.gapi) {
                showToast('Google API 未載入，請稍後重試', 'error');
                return;
            }
            gapi.load('picker', { 'callback': () => { pickerApiLoaded = true; createPicker(targetId); } });
            return;
        }

        if (!oauthToken) {
            showToast('未取得授權令牌，請重試', 'error');
            console.error('Missing OAuth token');
            return;
        }

        try {
            const view = new google.picker.DocsView(google.picker.ViewId.DOCS);
            view.setIncludeFolders(true);
            view.setSelectFolderEnabled(true);

            const picker = new google.picker.PickerBuilder()
                .enableFeature(google.picker.Feature.NAV_HIDDEN)
                .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
                .setAppId(config.google_app_id)
                .setOAuthToken(oauthToken)
                .addView(view)
                .setDeveloperKey(config.google_api_key)
                .setCallback((data) => pickerCallback(data, targetId))
                .build();

            picker.setVisible(true);
        } catch (e) {
            console.error('建立 Picker 失敗:', e);
            showToast('瀏覽工具啟動失敗：' + e.message, 'error');
        }
    }

    function pickerCallback(data, targetId) {
        if (data.action === google.picker.Action.PICKED) {
            if (!data.docs || data.docs.length === 0) {
                showToast('未選取任何資料夾', 'warning');
                return;
            }
            const folder = data.docs[0];
            const input = document.getElementById(targetId);
            if (input) {
                input.value = folder.id;
                input.dispatchEvent(new Event('change'));
                showToast(`已選取資料夾：${folder.name}`);
                // 當選擇輸入資料夾時，自動加載該資料夾的協作記憶
                if (targetId === 'drive-folder-id') {
                    autoLoadCollaborativeMemoryForDrive(folder.id);
                }
                const onPicked = pickerSelectionCallback;
                pickerSelectionCallback = null;
                if (onPicked) onPicked(folder);
            }
        } else if (data.action === google.picker.Action.CANCEL) {
            pickerSelectionCallback = null;
            console.log('Picker 已取消');
        } else if (data[google.picker.Response.ERROR_CODE]) {
            pickerSelectionCallback = null;
            const errorCode = data[google.picker.Response.ERROR_CODE];
            console.error('Picker 錯誤代碼:', errorCode);
            showToast(`Picker 錯誤：${errorCode}`, 'error');
        }
    }

    // 自動加載 Google Drive 資料夾的協作記憶
    async function autoLoadCollaborativeMemoryForDrive(folderId) {
        try {
            const response = await fetch(`/drive/collaborative_memory/get/?folder_id=${encodeURIComponent(folderId)}`);
            if (!response.ok) {
                // 資料夾沒有協作記憶，清空內存版本
                window._collaborativeMemories.drive = '';
                return;
            }

            const data = await response.json();
            if (data.content) {
                window._collaborativeMemories.drive = data.content;
            } else {
                window._collaborativeMemories.drive = '';
            }
        } catch (e) {
            console.warn('無法自動加載協作記憶:', e);
        }
    }

    // Initialize GAPI
    function loadPicker() {
        gapi.load('picker', { 'callback': () => { pickerApiLoaded = true; } });
    }

    // Check if script is already ready
    if (window.gapi) {
        loadPicker();
    } else {
        // Wait for script to load (added in index.html)
        const checkGapi = setInterval(() => {
            if (window.gapi) {
                loadPicker();
                clearInterval(checkGapi);
            }
        }, 500);
    }

    window.__showMetricsSummary = function() {
        const summaryPanel = document.getElementById('batch-metrics-summary');
        if (!summaryPanel) return;
        if (!currentBatchResults || currentBatchResults.length === 0) return;
        if (!currentBatchResults.some(publicClassificationWasRun)) {
            showToast('本次未執行可公開性判定，沒有判定指標', 'error');
            return;
        }

        const computed = computeFrontendMetrics(currentBatchResults);
        renderMetricsSummary(computed);
        summaryPanel.classList.remove('hidden');
        summaryPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    function computeFrontendMetrics(results) {
        const labels = ['safe', 'unsafe', 'pending'];

        // 初始化混淆矩陣
        const cm = {};
        labels.forEach(t => { cm[t] = {}; labels.forEach(p => { cm[t][p] = 0; }); });

        // 決策分佈
        const userDist = { safe: 0, unsafe: 0, pending: 0 };
        const aiDist = { safe: 0, unsafe: 0, pending: 0 };

        // 分析統計
        let imagesWithFaces = 0, imagesWithStraps = 0, totalFaces = 0, totalStraps = 0;

        // 已覆寫的檔案
        const changedFiles = [];

        results.forEach((item, idx) => {
            const ai = item.ai_decision || 'safe';
            const user = item.user_decision || ai;

            if (labels.includes(ai) && labels.includes(user)) {
                cm[ai][user]++;
                userDist[user]++;
                aiDist[ai]++;
            }

            if (ai !== user) {
                changedFiles.push({
                    index: idx + 1,
                    file_name: item.file_name || item.file || 'unknown',
                    ai_decision: ai,
                    user_decision: user
                });
            }

            // 分析統計（Drive 模式資料在 item.result，本地模式在 item 本身）
            const analysis = item.result || item;
            if (analysis.has_face) { imagesWithFaces++; totalFaces += (analysis.face_bboxes?.length || analysis.face_count || 0); }
            if (analysis.has_brand_strap) { imagesWithStraps++; totalStraps += (analysis.strap_bboxes?.length || 0); }
        });

        // 計算 precision / recall / F1
        const classMetrics = {};
        labels.forEach(label => {
            const tp = cm[label][label];
            const fp = labels.reduce((s, l) => l !== label ? s + cm[l][label] : s, 0);
            const fn = labels.reduce((s, l) => l !== label ? s + cm[label][l] : s, 0);
            const precision = (tp + fp) > 0 ? tp / (tp + fp) : 0;
            const recall    = (tp + fn) > 0 ? tp / (tp + fn) : 0;
            const f1        = (precision + recall) > 0 ? 2 * precision * recall / (precision + recall) : 0;
            classMetrics[label] = { precision, recall, f1_score: f1, support: tp + fn };
        });

        const total = results.length;
        const agreed = results.filter(r => (r.ai_decision || 'safe') === (r.user_decision || r.ai_decision || 'safe')).length;

        return {
            metrics: {
                total_processed: total,
                total_errors: 0,
                timestamp: { duration_seconds: 0 },
                confusion_matrix: cm,
                metrics: classMetrics,
                agreement_rate: total > 0 ? agreed / total : 0,
                changed_count: changedFiles.length,
                decision_distribution: { user_decisions: userDist, ai_decisions: aiDist }
            },
            analysis_stats: {
                images_with_faces: imagesWithFaces,
                images_with_straps: imagesWithStraps,
                average_faces_per_image: total > 0 ? totalFaces / total : 0,
                average_straps_per_image: total > 0 ? totalStraps / total : 0
            },
            changed_files: changedFiles
        };
    }

    window.__toggleMetricsSummary = function() {
        const content = document.getElementById('metrics-content');
        if (!content) return;
        content.style.display = content.style.display === 'none' ? '' : 'none';
    };

    function renderMetricsSummary(data) {
        const metrics = data.metrics || {};
        const stats = data.analysis_stats || {};
        const changedFiles = data.changed_files || [];

        // Processing Stats
        document.getElementById('metrics-total').textContent = metrics.total_processed || 0;
        document.getElementById('metrics-success').textContent = metrics.total_processed || 0;
        document.getElementById('metrics-failed').textContent = metrics.total_errors || 0;

        const duration = metrics.timestamp?.duration_seconds || 0;
        document.getElementById('metrics-duration').textContent = duration > 60 
            ? `${Math.floor(duration / 60)}m ${Math.floor(duration % 60)}s`
            : `${duration.toFixed(1)}s`;

        // Decision Distribution
        const userDist = metrics.decision_distribution?.user_decisions || {};
        document.getElementById('metrics-dist-safe').textContent = userDist.safe || 0;
        document.getElementById('metrics-dist-pending').textContent = userDist.pending || 0;
        document.getElementById('metrics-dist-unsafe').textContent = userDist.unsafe || 0;

        // Agreement Analysis
        const agreementRate = (metrics.agreement_rate || 0) * 100;
        document.getElementById('metrics-agreement-rate').textContent = agreementRate.toFixed(1) + '%';
        document.getElementById('metrics-changed-count').textContent = metrics.changed_count || 0;
        document.querySelector('.agreement-fill').style.width = agreementRate + '%';

        // Confusion Matrix
        renderConfusionMatrix(metrics.confusion_matrix || {});

        // Performance Metrics
        renderPerformanceMetrics(metrics.metrics || {});

        // Analysis Stats
        document.getElementById('metrics-with-faces').textContent = stats.images_with_faces || 0;
        document.getElementById('metrics-avg-faces').textContent = (stats.average_faces_per_image || 0).toFixed(2);
        document.getElementById('metrics-with-straps').textContent = stats.images_with_straps || 0;
        document.getElementById('metrics-avg-straps').textContent = (stats.average_straps_per_image || 0).toFixed(2);

        // Changed Files
        renderChangedFilesList(changedFiles);

        // Store current metrics for export
        window._currentMetrics = { metrics, stats, changedFiles };
    }

    function renderConfusionMatrix(cm) {
        const container = document.getElementById('metrics-confusion-matrix');
        if (!container) return;

        const labels = ['safe', 'unsafe', 'pending'];
        let html = '<table>';
        
        // Header
        html += '<tr><th>實際/預測</th>';
        labels.forEach(label => {
            const displayLabel = label === 'safe' ? '可公開' : label === 'unsafe' ? '不可公開' : '待確認';
            html += `<th>${displayLabel}</th>`;
        });
        html += '</tr>';

        // Rows
        labels.forEach(trueLabel => {
            const displayLabel = trueLabel === 'safe' ? '可公開' : trueLabel === 'unsafe' ? '不可公開' : '待確認';
            html += `<tr><td class="matrix-label">${displayLabel}</td>`;
            labels.forEach(predLabel => {
                const count = cm[trueLabel]?.[predLabel] || 0;
                const isCorrect = trueLabel === predLabel;
                const cellClass = isCorrect ? 'diagonal' : '';
                html += `<td class="${cellClass}">${count}</td>`;
            });
            html += '</tr>';
        });

        html += '</table>';
        container.innerHTML = html;
    }

    function renderPerformanceMetrics(metricsDict) {
        const container = document.getElementById('metrics-performance');
        if (!container) return;

        const labels = ['safe', 'unsafe', 'pending'];
        const labelNames = { safe: '可公開', unsafe: '不可公開', pending: '待確認' };

        let html = '';
        labels.forEach(label => {
            const m = metricsDict[label] || {};
            html += `<div class="metric-row">
                <div class="class-label">${labelNames[label]}</div>
                <div></div>
                <div class="metric-cell">
                    <span class="cell-label">精準度</span>
                    <strong>${(m.precision || 0).toFixed(3)}</strong>
                </div>
                <div class="metric-cell">
                    <span class="cell-label">召回率</span>
                    <strong>${(m.recall || 0).toFixed(3)}</strong>
                </div>
                <div class="metric-cell">
                    <span class="cell-label">F1分數</span>
                    <strong>${(m.f1_score || 0).toFixed(3)}</strong>
                </div>
            </div>`;
        });

        container.innerHTML = html;
    }

    function renderChangedFilesList(changedFiles) {
        const container = document.getElementById('metrics-changed-files');
        if (!container) return;

        if (!changedFiles || changedFiles.length === 0) {
            container.innerHTML = '<div class="no-changes">沒有被覆寫的檔案</div>';
            return;
        }

        let html = '';
        changedFiles.forEach(file => {
            const aiDecisionClass = 'badge-ai';
            const aiLabel = file.ai_decision === 'safe' ? '可公開' : file.ai_decision === 'unsafe' ? '不可公開' : '待確認';
            const userLabel = file.user_decision === 'safe' ? '可公開' : file.user_decision === 'unsafe' ? '不可公開' : '待確認';
            const userClass = file.user_decision === 'safe' ? 'badge-user safe' : file.user_decision === 'unsafe' ? 'badge-user unsafe' : 'badge-user pending';

            html += `<div class="changed-file-item">
                <div class="changed-file-name" title="${file.file_name}">${file.file_name}</div>
                <div class="changed-decision-badge ${aiDecisionClass}">${aiLabel}</div>
                <div class="changed-decision-badge ${userClass}">${userLabel}</div>
            </div>`;
        });

        container.innerHTML = html;
    }

    window.__exportMetricsJSON = function() {
        if (!window._currentMetrics) {
            showToast('尚無指標資料', 'error');
            return;
        }
        try {
            const json = JSON.stringify(window._currentMetrics, null, 2);
            const blob = new Blob([json], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `batch_summary_${Date.now()}.json`;
            a.click();
            URL.revokeObjectURL(url);
            showToast('已匯出 JSON', 'success');

        } catch (e) {
            console.error('Export failed:', e);
            showToast('匯出失敗：' + e.message, 'error');
        }
    };

    window.__exportMetricsCSV = function() {
        const metrics = window._currentMetrics;
        if (!metrics) {
            showToast('尚無指標資料', 'error');
            return;
        }

        try {
            // Simple CSV export of metrics
            let csv = '批量辨識結果摘要\n\n';

            // Processing Stats
            csv += '處理統計\n';
            csv += `總共處理,${metrics.metrics.total_processed}\n`;
            csv += `失敗,${metrics.metrics.total_errors}\n`;
            csv += `處理時間(秒),${metrics.metrics.timestamp?.duration_seconds || 0}\n\n`;

            // Decision Distribution
            csv += '決策分佈\n';
            const userDist = metrics.metrics.decision_distribution?.user_decisions || {};
            csv += `可公開(Safe),${userDist.safe || 0}\n`;
            csv += `待確認(Pending),${userDist.pending || 0}\n`;
            csv += `不可公開(Unsafe),${userDist.unsafe || 0}\n\n`;

            // Performance Metrics
            csv += '分類性能指標\n';
            csv += '類別,精準度,召回率,F1分數\n';
            const metricsDict = metrics.metrics.metrics || {};
            ['safe', 'unsafe', 'pending'].forEach(label => {
                const m = metricsDict[label] || {};
                csv += `${label},${(m.precision || 0).toFixed(3)},${(m.recall || 0).toFixed(3)},${(m.f1_score || 0).toFixed(3)}\n`;
            });

            csv += '\n分析統計\n';
            csv += `含人臉圖片,${metrics.stats.images_with_faces || 0}\n`;
            csv += `平均人臉數,${(metrics.stats.average_faces_per_image || 0).toFixed(2)}\n`;
            csv += `含名牌圖片,${metrics.stats.images_with_straps || 0}\n`;
            csv += `平均名牌數,${(metrics.stats.average_straps_per_image || 0).toFixed(2)}\n`;

            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `batch_summary_${window._currentSessionId || 'export'}.csv`;
            a.click();
            URL.revokeObjectURL(url);
            showToast('已匯出 CSV', 'success');

        } catch (e) {
            console.error('CSV export failed:', e);
            showToast('CSV 匯出失敗：' + e.message, 'error');
        }
    };

    // 儲存各模式的協作記憶
    window._collaborativeMemories = {
        single: '',
        local: '',
        drive: ''
    };

    // 協作記憶相關函數
    window.__openCollaborativeMemoryModal = async function(mode) {
        const modal = document.getElementById('collaborative-memory-modal');
        const textarea = document.getElementById('collaborative-memory-text');
        const title = document.getElementById('memory-modal-title');
        const hint = document.getElementById('memory-modal-hint');

        modal.setAttribute('data-mode', mode);

        if (mode === 'drive') {
            // Google Drive 模式
            const folderInput = document.getElementById('drive-folder-id');
            const folderId = folderInput.value.trim();

            title.textContent = '📝 編輯協作記憶';
            hint.textContent = '在此輸入針對此資料夾的特殊辨識規則或背景信息（最多 1000 字）。' +
                              (folderId ? '這些信息將保存到雲端。' : '未指定資料夾時，信息僅在本次會話有效。');

            try {
                if (folderId) {
                    // 有指定資料夾時，嘗試從遠端讀取
                    const response = await fetch(`/drive/collaborative_memory/get/?folder_id=${encodeURIComponent(folderId)}`);
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}`);
                    }

                    const data = await response.json();
                    textarea.value = data.content || '';
                } else {
                    // 沒有指定資料夾時，從內存讀取
                    textarea.value = window._collaborativeMemories.drive || '';
                }
            } catch (e) {
                console.warn('無法加載協作記憶文件:', e);
                textarea.value = window._collaborativeMemories.drive || '';
            }
        } else if (mode === 'single') {
            title.textContent = '📝 添加協作提示詞（單張辨識）';
            hint.textContent = '輸入特殊的辨識規則或背景信息以增強 AI 判定準確性。這些信息僅在本次辨識中使用。';
            textarea.value = window._collaborativeMemories.single || '';
        } else if (mode === 'local') {
            title.textContent = '📝 添加協作提示詞（本地批量）';
            hint.textContent = '輸入特殊的辨識規則或背景信息以增強 AI 判定準確性。這些信息僅在本次批量辨識中使用。';
            textarea.value = window._collaborativeMemories.local || '';
        }

        updateCharCount();
        modal.classList.remove('hidden');
    };

    window.__closeCollaborativeMemoryModal = function() {
        const modal = document.getElementById('collaborative-memory-modal');
        modal.classList.add('hidden');
    };

    window.__saveCollaborativeMemory = async function() {
        const modal = document.getElementById('collaborative-memory-modal');
        const mode = modal.getAttribute('data-mode');
        const textarea = document.getElementById('collaborative-memory-text');
        const content = textarea.value.trim();

        if (content.length > 1000) {
            showToast('內容超過 1000 字限制', 'error');
            return;
        }

        try {
            if (mode === 'drive') {
                // Google Drive 模式
                const folderInput = document.getElementById('drive-folder-id');
                const folderId = folderInput.value.trim();

                if (folderId) {
                    // 有指定資料夾：保存到遠端
                    const formData = new FormData();
                    formData.append('folder_id', folderId);
                    formData.append('content', content);

                    const response = await fetch('/drive/collaborative_memory/save/', {
                        method: 'POST',
                        body: formData
                    });

                    if (!response.ok) {
                        const error = await response.json();
                        throw new Error(error.detail || `HTTP ${response.status}`);
                    }

                    const data = await response.json();
                    showToast('協作記憶已' + (data.status === 'created' ? '建立' : '更新'), 'success');
                    window._collaborativeMemories.drive = content;  // 同時存到內存作為備份
                } else {
                    // 沒有指定資料夾：存到內存
                    window._collaborativeMemories.drive = content;
                    showToast('已添加協作提示詞（本次會話有效）', 'success');
                }
            } else if (mode === 'single') {
                // 單張模式：存到記憶體，不保存
                window._collaborativeMemories.single = content;
                showToast('已添加協作提示詞（本次辨識有效）', 'success');
            } else if (mode === 'local') {
                // 本地模式：存到記憶體，不保存
                window._collaborativeMemories.local = content;
                showToast('已添加協作提示詞（本次批量有效）', 'success');
            }

            window.__closeCollaborativeMemoryModal();
        } catch (e) {
            console.error('保存協作記憶失敗:', e);
            showToast('保存失敗：' + e.message, 'error');
        }
    };

    // 字符計數
    function updateCharCount() {
        const textarea = document.getElementById('collaborative-memory-text');
        const count = document.getElementById('char-count');
        count.textContent = textarea.value.length;
    }

    // 字符計數事件監聽
    const textarea = document.getElementById('collaborative-memory-text');
    if (textarea) {
        textarea.addEventListener('input', updateCharCount);
    }

    // 監聽 Google Drive 資料夾 ID 的變化，自動加載協作記憶
    const driveFolderIdInput = document.getElementById('drive-folder-id');
    if (driveFolderIdInput) {
        driveFolderIdInput.addEventListener('change', (e) => {
            const folderId = e.target.value.trim();
            if (folderId) {
                autoLoadCollaborativeMemoryForDrive(folderId);
            } else {
                // 清空資料夾時，清空協作記憶
                window._collaborativeMemories.drive = '';
            }
        });
    }

    // 綁定編輯按鈕（Google Drive 模式）
    const editMemoryBtn = document.getElementById('btn-edit-collaborative-memory');
    if (editMemoryBtn) {
        editMemoryBtn.addEventListener('click', () => window.__openCollaborativeMemoryModal('drive'));
    }

    // 綁定添加按鈕（單張模式）
    const addMemorySingleBtn = document.getElementById('btn-add-memory-single');
    if (addMemorySingleBtn) {
        addMemorySingleBtn.addEventListener('click', () => window.__openCollaborativeMemoryModal('single'));
    }

    // 綁定添加按鈕（本地批量模式）
    const addMemoryLocalBtn = document.getElementById('btn-add-memory-local');
    if (addMemoryLocalBtn) {
        addMemoryLocalBtn.addEventListener('click', () => window.__openCollaborativeMemoryModal('local'));
    }

});
