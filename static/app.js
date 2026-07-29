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
    const analyzeBatchBtn = document.getElementById('analyze-batch-btn');
    const organizeArea = document.getElementById('organize-area');
    const safeFolder = document.getElementById('safe-folder');
    const unsafeFolder = document.getElementById('unsafe-folder');
    const organizeBtn = document.getElementById('organize-btn');

    // Drive Elements
    const driveFolderId = document.getElementById('drive-folder-id');
    const driveTargetId = document.getElementById('drive-target-id');

    // === Batch Source Switching ===
    batchSourceRadios.forEach(radio => {
        radio.addEventListener('change', (e) => {
            if (e.target.value === 'local') {
                localBatchInputs.classList.remove('hidden');
                driveBatchInputs.classList.add('hidden');
            } else {
                localBatchInputs.classList.add('hidden');
                driveBatchInputs.classList.remove('hidden');
            }
        });
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
        if (e.key === 'Escape') toggleFullscreen(false);
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

    let batchSelectedFiles = [];

    const downloadBatchResultsBtn = document.getElementById('download-batch-results-btn');
    const includeAnnotatedDownload = document.getElementById('include-annotated-download');
    let config = null;

    function getBatchUploadLimits() {
        return {
            maxFiles: config?.batch_upload_max_files || 3,
            maxFileBytes: (config?.batch_upload_max_file_mb || 2) * 1024 * 1024,
            maxTotalBytes: (config?.batch_upload_max_total_mb || 4) * 1024 * 1024,
            defaultConcurrency: config?.batch_upload_concurrency || 2,
        };
    }

    function validateBatchFiles(files) {
        const limits = getBatchUploadLimits();
        if (files.length > limits.maxFiles) return `一次最多選 ${limits.maxFiles} 張照片`;
        const oversized = files.find(file => file.size > limits.maxFileBytes);
        if (oversized) return `${oversized.name} 超過單檔大小限制`;
        const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
        if (totalBytes > limits.maxTotalBytes) return '選取照片的總大小超過限制';
        return null;
    }

    function selectBatchFiles(files) {
        const images = Array.from(files).filter(file => file.type.startsWith('image/'));
        const error = validateBatchFiles(images);
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

    window.addEventListener('storage', event => {
        if (event.key === sharedBusyKey) syncRemoteBusyState();
    });
    window.setInterval(syncRemoteBusyState, 5000);
    window.addEventListener('pagehide', endSharedBusy);

    function showLoading(show) {
        if (show) {
            if (!loadingControlStates) {
                loadingControlStates = new Map();
                document.querySelectorAll('button, input, select, textarea').forEach(control => {
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

    function updateProgressUI(current, total, success, failed) {
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

    // === Batch Mode Handling ===
    analyzeBatchBtn.addEventListener('click', async () => {
        const source = document.querySelector('input[name="batch-source"]:checked').value;
        batchMode = source === 'local' ? 'upload' : source;
        const currentConcurrency = parseInt(batchConcurrency.value) || 3;

        // Generate session_id for metrics tracking
        const sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        window._currentSessionId = sessionId;

        let endpoint = '/batch_upload_stream/';
        let body = {};
        let requestOptions;

        if (source === 'local') {
            const validationError = validateBatchFiles(batchSelectedFiles);
            if (batchSelectedFiles.length === 0 || validationError) {
                showToast(validationError || '請先選擇這場活動的照片', 'error');
                return;
            }
            const formData = new FormData();
            batchSelectedFiles.forEach(file => formData.append('files', file, file.name));
            formData.append('concurrency', String(currentConcurrency));
            formData.append('color_rules_json', JSON.stringify(colorSwatches));
            formData.append('session_id', sessionId);
            const memory = window._collaborativeMemories?.local;
            if (memory) formData.append('collaborative_memory', memory);
            requestOptions = { method: 'POST', body: formData };
        } else {
            const fId = driveFolderId.value.trim();
            const tId = driveTargetId.value.trim();
            if (!fId) {
                showToast('請先選一個 Google 雲端資料夾', 'error');
                return;
            }
            endpoint = '/batch_drive_stream/'; // 切換到串流 API
            body = {
                folder_id: fId,
                target_folder_id: tId || null,
                concurrency: currentConcurrency,
                color_rules: colorSwatches,
                session_id: sessionId,
                collaborative_memory: window._collaborativeMemories?.drive || null,
            };
            requestOptions = {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            };
        }

        if (!(await beginSharedBusy('辨識整場活動'))) return;

        // Reset and Show Progress UI
        updateProgressUI(0, 0, 0, 0);
        currentBatchResults = [];
        currentFaceClusters = [];
        selectedFaceClusterId = null;
        faceClusteringInfo = null;
        batchOverviewActive = false;
        window._currentMetrics = null;
        window._currentSessionId = sessionId;
        document.getElementById('batch-overview').classList.add('hidden');
        document.getElementById('back-to-overview-btn').classList.add('hidden');
        document.getElementById('batch-metrics-summary').classList.add('hidden');
        reviewSummary.style.display = 'none';
        decisionButtons.style.display = 'none';
        splitViewer.classList.add('hidden');
        emptyState.classList.remove('hidden');
        showLoading(true);
        document.getElementById('loading-text').textContent = '正在翻開這場活動的照片…';

        try {
            const response = await fetch(endpoint, requestOptions);

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || '沒能開始，再試一次好嗎');
            }

            // 處理串流結果
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let successCount = 0;
            let failedCount = 0;
            let totalImages = 0;
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
                        if (data.status === 'ok') {
                            // Drive 串流模式：每行一筆 NDJSON
                            successCount++;
                            totalImages = data.total;
                            const result = data.result || data;
                            const aiStatus = result.moderation_status || (result.is_safe_for_public ? 'public' : 'private');
                            // 計算 ai_decision（如果還沒有）
                            if (!result.ai_decision) {
                                result.ai_decision = aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending';
                            }
                            data.user_decision = data.user_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                            data.ai_decision = result.ai_decision;
                            // 保持 session_id
                            if (data.session_id) window._currentSessionId = data.session_id;
                            currentBatchResults.push(data);
                        } else if (data.status === 'error') {
                            failedCount++;
                            totalImages = data.total || totalImages;
                            showToast(`${data.file_name || data.file} 這張沒看成`, 'error');
                        } else if (data.status === 'completed') {
                            currentFaceClusters = Array.isArray(data.face_clusters) ? data.face_clusters : [];
                            faceClusteringInfo = data.face_clustering || null;
                            selectedFaceClusterId = currentFaceClusters[0]?.cluster_id || null;
                        } else if (data.results && Array.isArray(data.results)) {
                            // 本機批次模式：一次性完整 JSON 回應
                            totalImages = data.total || data.results.length;
                            if (data.temp_folder) currentTempFolder = data.temp_folder;
                            if (data.session_id) window._currentSessionId = data.session_id;
                            data.results.forEach(item => {
                                if (item.status === 'ok') {
                                    const aiStatus = item.moderation_status || (item.is_safe_for_public ? 'public' : 'private');
                                    item.user_decision = item.user_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                                    item.ai_decision = item.ai_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                                    currentBatchResults.push(item);
                                    successCount++;
                                } else {
                                    failedCount++;
                                    showToast(`${item.file} 這張沒看成`, 'error');
                                }
                            });
                            currentFaceClusters = Array.isArray(data.face_clusters) ? data.face_clusters : [];
                            faceClusteringInfo = data.face_clustering || null;
                            selectedFaceClusterId = currentFaceClusters[0]?.cluster_id || null;
                        }

                        // 更新 UI 進度
                        updateProgressUI(successCount + failedCount, totalImages, successCount, failedCount);

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
                            const aiStatus = item.moderation_status || (item.is_safe_for_public ? 'public' : 'private');
                            item.user_decision = item.user_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                            item.ai_decision = item.ai_decision || (aiStatus === 'public' ? 'safe' : aiStatus === 'private' ? 'unsafe' : 'pending');
                            currentBatchResults.push(item);
                            successCount++;
                        } else {
                            failedCount++;
                            showToast(`${item.file} 這張沒看成`, 'error');
                        }
                    });
                    currentFaceClusters = Array.isArray(data.face_clusters) ? data.face_clusters : [];
                    faceClusteringInfo = data.face_clustering || null;
                    selectedFaceClusterId = currentFaceClusters[0]?.cluster_id || null;
                    updateProgressUI(successCount + failedCount, totalImages, successCount, failedCount);
                }
            }

            showToast(`看完了。${successCount} 張看過${failedCount ? `，${failedCount} 張沒看成` : ''}`);

            if (source === 'local') {
                organizeArea.classList.add('hidden');
            } else {
                organizeArea.classList.add('hidden');
            }

            if (currentBatchResults.length > 0) {
                showBatchOverview();
                // metrics 等用戶確認後再顯示
            }

        } catch (e) {
            showToast(e.message, 'error');
        } finally {
            showLoading(false);
            endSharedBusy();
            document.getElementById('loading-text').textContent = '正在一張一張看過去…';
        }
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

            // 輔助 UI: 更新統計數據
            const analysis = currentData.result;
            updateStatsUI(currentData.file_name, analysis);
        } else {
            // Local File Mode (Non-Stream)
            originalImg.src = `/local_file/?path=${encodeURIComponent(currentData.original_path)}`;
            annotatedImg.src = `/local_file/?path=${encodeURIComponent(currentData.output)}`;

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
            else if (r.user_decision === 'pending') pendingC++;
            else unsafeC++;
        });
        document.getElementById('ov-total').textContent = currentBatchResults.length;
        document.getElementById('ov-safe').textContent = safeC;
        document.getElementById('ov-unsafe').textContent = unsafeC;
        document.getElementById('ov-pending').textContent = pendingC;

        const content = document.getElementById('overview-content');
        const viewToggle = document.querySelector('.view-mode-toggle');
        if (currentFaceClusters.length > 0) {
            viewToggle?.classList.add('hidden');
            renderFaceWorkspace(content);
        } else if (faceClusteringInfo && faceClusteringInfo.available === false) {
            viewToggle?.classList.remove('hidden');
            content.innerHTML = `<div class="face-workspace-notice">
                <strong>人物分群目前未啟用</strong>
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

    function renderFaceWorkspace(container) {
        const selected = currentFaceClusters.find(cluster => cluster.cluster_id === selectedFaceClusterId) || currentFaceClusters[0];
        if (!selected) return;
        selectedFaceClusterId = selected.cluster_id;
        const clusterItems = currentFaceClusters.map(cluster => `
            <button class="face-cluster-item ${cluster.cluster_id === selected.cluster_id ? 'selected' : ''}"
                    type="button" data-cluster-id="${escapeHtml(cluster.cluster_id)}">
                <span class="face-cluster-avatar">${String(cluster.display_name || '人').trim().charAt(0) || '人'}</span>
                <span class="face-cluster-copy">
                    <strong>${escapeHtml(cluster.display_name)}</strong>
                    <small>${cluster.photo_count} 張照片 · ${cluster.face_count} 張臉</small>
                </span>
                <span class="face-cluster-status status-${escapeHtml(cluster.status)}">${statusLabel(cluster.status)}</span>
            </button>`).join('');
        const evidence = (selected.evidence_photos || []).map(item => {
            const resultIndex = findResultIndex(item.file_name);
            const source = item.image_b64 ? `data:image/jpeg;base64,${item.image_b64}` : (resultIndex >= 0 ? getItemImgSrc(currentBatchResults[resultIndex]) : '');
            return `<button class="face-evidence-card" type="button" data-result-index="${resultIndex}" ${resultIndex < 0 ? 'disabled' : ''}>
                ${source ? `<img src="${source}" alt="${escapeHtml(item.file_name)}" loading="lazy">` : '<span class="face-evidence-missing">無預覽</span>'}
                <span>${escapeHtml(item.file_name)}</span>
                <small>信心 ${(Number(item.score || 0) * 100).toFixed(0)}%</small>
            </button>`;
        }).join('');
        container.innerHTML = `<section class="face-workspace" aria-label="人物分類工作台">
            <aside class="face-cluster-list-panel">
                <div class="face-panel-heading"><span>人物群組</span><strong>${currentFaceClusters.length}</strong></div>
                <div class="face-cluster-list">${clusterItems}</div>
            </aside>
            <main class="face-evidence-panel">
                <div class="face-panel-heading"><span>${escapeHtml(selected.display_name)} 的出現位置</span><small>${selected.photo_count} 張照片</small></div>
                <div class="face-evidence-grid">${evidence || '<p class="face-empty-copy">沒有可顯示的證據照片</p>'}</div>
            </main>
            <aside class="face-decision-panel">
                <div class="face-panel-heading"><span>確認人物</span></div>
                <label>人物名稱<input id="face-cluster-name" value="${escapeHtml(selected.display_name)}" maxlength="80"></label>
                <label>確認狀態<select id="face-cluster-status">
                    <option value="unconfirmed" ${selected.status === 'unconfirmed' ? 'selected' : ''}>未命名</option>
                    <option value="pending" ${selected.status === 'pending' ? 'selected' : ''}>待確認</option>
                    <option value="confirmed" ${selected.status === 'confirmed' ? 'selected' : ''}>已確認</option>
                </select></label>
                <label>備註<textarea id="face-cluster-notes" rows="4" maxlength="500" placeholder="例如：講師、工作人員">${escapeHtml(selected.notes || '')}</textarea></label>
                <button id="save-face-cluster-btn" class="face-save-btn" type="button">儲存這個人物</button>
                <small class="face-save-hint">流水 ID：${escapeHtml(selected.cluster_id)}</small>
            </aside>
        </section>`;

        container.querySelectorAll('.face-cluster-item').forEach(button => {
            button.addEventListener('click', () => {
                selectedFaceClusterId = button.dataset.clusterId;
                renderFaceWorkspace(container);
            });
        });
        container.querySelectorAll('.face-evidence-card').forEach(button => {
            button.addEventListener('click', () => {
                const resultIndex = Number(button.dataset.resultIndex);
                if (resultIndex >= 0) openReviewFromOverview(resultIndex);
            });
        });
        document.getElementById('save-face-cluster-btn')?.addEventListener('click', saveSelectedFaceCluster);
    }

    async function saveSelectedFaceCluster() {
        const cluster = currentFaceClusters.find(item => item.cluster_id === selectedFaceClusterId);
        if (!cluster) return;
        const update = {
            display_name: document.getElementById('face-cluster-name').value.trim() || cluster.display_name,
            status: document.getElementById('face-cluster-status').value,
            notes: document.getElementById('face-cluster-notes').value.trim(),
        };
        Object.assign(cluster, update);
        renderBatchOverview();
        try {
            const response = await fetch(`/face_clusters/${encodeURIComponent(window._currentSessionId)}/${encodeURIComponent(cluster.cluster_id)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(update),
            });
            if (!response.ok) throw new Error('server update failed');
            showToast(`已儲存「${update.display_name}」`);
        } catch (error) {
            console.warn('Face cluster update stayed in browser only:', error);
            showToast('名稱已保留在這次下載資料中；伺服器同步失敗', 'error');
        }
    }

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

    function buildBatchResultExport() {
        return {
            exported_at: new Date().toISOString(),
            session_id: window._currentSessionId || null,
            batch_mode: batchMode,
            image_count: currentBatchResults.length,
            face_clusters: currentFaceClusters,
            results: currentBatchResults.map((item, index) => {
                const { original_image_b64, drawn_image_b64, ...result } = item;
                return {
                    index: index + 1,
                    ...result,
                    annotated_image_available: Boolean(drawn_image_b64),
                };
            }),
        };
    }

    async function downloadBatchResults() {
        if (currentBatchResults.length === 0) {
            showToast('目前沒有可下載的辨識結果', 'error');
            return;
        }

        const exportData = buildBatchResultExport();
        const json = JSON.stringify(exportData, null, 2);
        const jsonBlob = new Blob([json], { type: 'application/json;charset=utf-8' });
        const wantsImages = Boolean(includeAnnotatedDownload?.checked);
        const imageBytes = currentBatchResults.reduce(
            (total, item) => total + base64ByteLength(item.drawn_image_b64),
            0,
        );
        const maxBytes = (config?.batch_download_max_mb || 8) * 1024 * 1024;

        if (!wantsImages) {
            downloadBlob(jsonBlob, `batch_results_${Date.now()}.json`);
            showToast('已下載 JSON 辨識結果', 'success');
            return;
        }

        if (imageBytes + jsonBlob.size > maxBytes || typeof JSZip === 'undefined') {
            downloadBlob(jsonBlob, `batch_results_${Date.now()}.json`);
            const reason = typeof JSZip === 'undefined' ? '壓縮元件未載入' : `後製圖超過 ${config?.batch_download_max_mb || 8}MB 上限`;
            showToast(`${reason}，已自動改下載 JSON；後製圖請改用雲端模式`, 'error');
            return;
        }

        downloadBatchResultsBtn.disabled = true;
        showLoading(true);
        document.getElementById('loading-text').textContent = '正在整理下載內容…';
        try {
            const zip = new JSZip();
            zip.file('result.json', json);
            const annotated = zip.folder('annotated');
            currentBatchResults.forEach((item, index) => {
                if (!item.drawn_image_b64) return;
                const sourceName = item.file_name || item.file || `image_${index + 1}.jpg`;
                const safeName = safeDownloadName(sourceName, `image_${index + 1}.jpg`).replace(/\.[^.]+$/, '');
                annotated.file(`annotated_${safeName}.jpg`, item.drawn_image_b64, { base64: true });
            });
            const zipBlob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 4 } });
            if (zipBlob.size > maxBytes) {
                downloadBlob(jsonBlob, `batch_results_${Date.now()}.json`);
                showToast(`結果包超過 ${config?.batch_download_max_mb || 8}MB 上限，已自動改下載 JSON；後製圖請改用雲端模式`, 'error');
                return;
            }
            downloadBlob(zipBlob, `batch_results_${Date.now()}.zip`);
            showToast('已下載 JSON 與後製圖 ZIP', 'success');
        } catch (error) {
            console.error('Batch result export failed:', error);
            downloadBlob(jsonBlob, `batch_results_${Date.now()}.json`);
            showToast('後製圖打包失敗，已自動改下載 JSON', 'error');
        } finally {
            showLoading(false);
            document.getElementById('loading-text').textContent = '正在一張一張看過去…';
            downloadBatchResultsBtn.disabled = false;
        }
    }

    downloadBatchResultsBtn?.addEventListener('click', downloadBatchResults);

    function renderThumbnailGrid(container) {
        let html = '<div class="thumbnail-grid">';
        currentBatchResults.forEach((item, idx) => {
            const decision = item.user_decision || 'private';
            const isOverride = item.user_decision !== item.ai_decision;
            const fileName = escapeHtml(item.file_name || item.file || `圖片 ${idx + 1}`);
            const src = getItemImgSrc(item);
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = decision === 'safe' ? '可以分享' : decision === 'pending' ? '之後再看' : '先留著';
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
            const decision = item.user_decision || 'private';
            const isOverride = item.user_decision !== item.ai_decision;
            const fileName = escapeHtml(item.file_name || item.file || `圖片 ${idx + 1}`);
            const src = getItemImgSrc(item);
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = decision === 'safe' ? '可以分享' : decision === 'pending' ? '之後再看' : '先留著';
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

    function openReviewFromOverview(index) {
        batchOverviewActive = false;
        currentIndex = index;
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
            renderBatchViewer();
            highlightCurrentInSummary();
        }
    });

    nextBtn.addEventListener('click', () => {
        if (currentBatchResults.length > 0) {
            currentIndex = (currentIndex + 1) % currentBatchResults.length;
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
        } else {
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
            const decision = item.user_decision || 'private';
            const isOverride = item.user_decision !== item.ai_decision;
            if (decision === 'safe') safeC++;
            else if (decision === 'pending') pendingC++;
            else unsafeC++;

            const fileName = item.file_name || item.file || `圖片 ${idx + 1}`;
            const currentClass = idx === currentIndex ? ' current' : '';
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = decision === 'safe' ? '可以分享' : decision === 'pending' ? '之後再看' : '先留著';

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
    // Fetch config on load
    async function fetchConfig() {
        try {
            const res = await fetch('/api/config');
            config = await res.json();
            console.log("Config loaded:", !!config.google_client_id);
            document.getElementById('batch-upload-limits').textContent =
                `最多 ${config.batch_upload_max_files} 張，單檔 ${config.batch_upload_max_file_mb}MB、合計 ${config.batch_upload_max_total_mb}MB 以內`;
            batchConcurrency.value = String(config.batch_upload_concurrency || 2);
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

    [btnBrowseSource, btnBrowseTarget].forEach(btn => {
        btn.addEventListener('click', () => {
            const targetInputId = btn.id === 'btn-browse-source' ? 'drive-folder-id' : 'drive-target-id';
            handleAuthClick(targetInputId);
        });
    });

    function handleAuthClick(targetId) {
        if (!config || !config.google_client_id) {
            showToast('伺服器未設定 Google Client ID', 'error');
            return;
        }
        if (!config.google_app_id) {
            showToast('伺服器未設定 Google Project Number', 'error');
            return;
        }

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
    tryFetchServerToken();

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
                showToast(`已選取資料夾：${folder.name}`);
                // 當選擇輸入資料夾時，自動加載該資料夾的協作記憶
                if (targetId === 'drive-folder-id') {
                    autoLoadCollaborativeMemoryForDrive(folder.id);
                }
            }
        } else if (data.action === google.picker.Action.CANCEL) {
            console.log('Picker 已取消');
        } else if (data[google.picker.Response.ERROR_CODE]) {
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
