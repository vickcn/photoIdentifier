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

    const inputFolder = document.getElementById('batch-input-folder');
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

    // === Temp Folder Management ===
    const tempFolderMgmt = document.getElementById('temp-folder-mgmt');
    const tempFolderSelect = document.getElementById('temp-folder-select');

    async function refreshTempFolders(inputDir) {
        if (!inputDir) return;
        try {
            const res = await fetch(`/review_temp_folders/?input_folder=${encodeURIComponent(inputDir)}`);
            if (!res.ok) return;
            const data = await res.json();
            const folders = data.folders || [];
            if (folders.length === 0) {
                tempFolderMgmt.classList.add('hidden');
                return;
            }
            tempFolderSelect.innerHTML = folders.map(f =>
                `<option value="${f.name}">${f.name}  (${f.size_mb} MB)</option>`
            ).join('');
            tempFolderMgmt.classList.remove('hidden');
        } catch (e) {
            console.warn('Failed to load temp folders', e);
        }
    }

    window.__refreshTempFolders = () => refreshTempFolders(inputFolder.value.trim());

    window.__clearSelectedTempFolder = async function () {
        const inputDir = inputFolder.value.trim();
        const folderName = tempFolderSelect.value;
        if (!inputDir || !folderName) return;
        if (!confirm(`確定要刪除暫存資料夾「${folderName}」嗎？`)) return;
        try {
            const res = await fetch('/delete_review_temp/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ input_folder: inputDir, folder_name: folderName })
            });
            if (!res.ok) throw new Error((await res.json()).detail);
            showToast('暫存資料夾已刪除');
            refreshTempFolders(inputDir);
        } catch (e) {
            showToast(e.message, 'error');
        }
    };

    // Refresh when user leaves input folder field
    inputFolder.addEventListener('blur', () => refreshTempFolders(inputFolder.value.trim()));

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

    function showLoading(show) {
        if (show) {
            loadingOverlay.classList.remove('hidden');
        } else {
            loadingOverlay.classList.add('hidden');
        }
    }

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

        showLoading(true);
        const formData = new FormData();
        formData.append('file', singleSelectedFile);
        formData.append('color_rules_json', JSON.stringify(colorSwatches));

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
            showToast('分析完成！');

        } catch (error) {
            showToast(error.message, 'error');
        } finally {
            showLoading(false);
        }
    });

    function updateStatsUI(filename, analysis) {
        fileNameDisplay.textContent = filename;

        const status = analysis.moderation_status || (analysis.is_safe_for_public ? 'public' : 'private');
        if (status === 'public') {
            safetyBadge.textContent = '可公開 (Safe)';
            safetyBadge.className = 'status-badge status-safe';
        } else if (status === 'pending') {
            safetyBadge.textContent = '待人員判定 (Pending)';
            safetyBadge.className = 'status-badge status-pending';
        } else {
            safetyBadge.textContent = '不可公開 (Unsafe)';
            safetyBadge.className = 'status-badge status-unsafe';
        }

        moderationReason.textContent = analysis.moderation_reason;
        faceCount.textContent = analysis.face_bboxes ? analysis.face_bboxes.length : 0;
        strapCount.textContent = analysis.strap_bboxes ? analysis.strap_bboxes.length : 0;
        strapColor.textContent = analysis.strap_color || '無';
    }


    // === DOM Progress Elements ===
    const progressFill = document.getElementById('progress-fill');
    const progressPercent = document.getElementById('progress-percent');
    const progressCount = document.getElementById('progress-count');
    const streamSuccessEl = document.getElementById('stream-success-count');
    const streamFailedEl = document.getElementById('stream-failed-count');
    const streamPendingEl = document.getElementById('stream-pending-count');

    function updateProgressUI(current, total, success, failed) {
        if (total === 0) return;
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
        batchMode = source;
        const currentConcurrency = parseInt(batchConcurrency.value) || 3;

        // Generate session_id for metrics tracking
        const sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        window._currentSessionId = sessionId;

        let endpoint = '/batch/';
        let body = {};

        if (source === 'local') {
            const inputDir = inputFolder.value.trim();
            if (!inputDir) {
                showToast('請填寫來源資料夾路徑', 'error');
                return;
            }
            body = {
                input_folder: inputDir,
                concurrency: currentConcurrency,
                color_rules: colorSwatches,
                session_id: sessionId,
            };
        } else {
            const fId = driveFolderId.value.trim();
            const tId = driveTargetId.value.trim();
            if (!fId) {
                showToast('請填寫 Google Drive 資料夾 ID', 'error');
                return;
            }
            endpoint = '/batch_drive_stream/'; // 切換到串流 API
            body = {
                folder_id: fId,
                target_folder_id: tId || null,
                concurrency: currentConcurrency,
                color_rules: colorSwatches,
                session_id: sessionId,
            };
        }

        // Reset and Show Progress UI
        updateProgressUI(0, 0, 0, 0);
        currentBatchResults = [];
        batchOverviewActive = false;
        document.getElementById('batch-overview').classList.add('hidden');
        document.getElementById('back-to-overview-btn').classList.add('hidden');
        reviewSummary.style.display = 'none';
        decisionButtons.style.display = 'none';
        splitViewer.classList.add('hidden');
        emptyState.classList.remove('hidden');
        showLoading(true);
        document.getElementById('loading-text').textContent = '正在啟動批量辨識引擎...';

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || '批量辨識啟動失敗');
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
                            showToast(`${data.file_name || data.file} 辨識出錯`, 'error');
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
                                    showToast(`${item.file} 辨識出錯`, 'error');
                                }
                            });
                        }

                        // 更新 UI 進度
                        updateProgressUI(successCount + failedCount, totalImages, successCount, failedCount);

                    } catch (err) {
                        console.error('JSON parsing data error:', line, err);
                    }
                }
            }

            showToast(`批量處理完成！成功：${successCount}，失敗：${failedCount}`);

            if (source === 'local') {
                organizeArea.classList.remove('hidden');
                refreshTempFolders(inputFolder.value.trim());
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
            document.getElementById('loading-text').textContent = '正在用 AI 深度辨識中...';
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

        document.getElementById('btn-view-thumbnail').classList.toggle('active', batchOverviewMode === 'thumbnail');
        document.getElementById('btn-view-list').classList.toggle('active', batchOverviewMode === 'list');

        const content = document.getElementById('overview-content');
        if (batchOverviewMode === 'thumbnail') {
            renderThumbnailGrid(content);
        } else {
            renderOverviewList(content);
        }
    }

    function getItemImgSrc(item) {
        if (item.original_image_b64) {
            return 'data:image/jpeg;base64,' + item.original_image_b64;
        }
        return `/local_file/?path=${encodeURIComponent(item.original_path)}`;
    }

    function renderThumbnailGrid(container) {
        let html = '<div class="thumbnail-grid">';
        currentBatchResults.forEach((item, idx) => {
            const decision = item.user_decision || 'private';
            const isOverride = item.user_decision !== item.ai_decision;
            const fileName = item.file_name || item.file || `圖片 ${idx + 1}`;
            const src = getItemImgSrc(item);
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = decision === 'safe' ? 'Safe' : decision === 'pending' ? 'Pending' : 'Unsafe';
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
            const fileName = item.file_name || item.file || `圖片 ${idx + 1}`;
            const src = getItemImgSrc(item);
            const badgeClass = decision === 'safe' ? 'safe' : decision === 'pending' ? 'pending' : 'unsafe';
            const badgeText = decision === 'safe' ? 'Safe' : decision === 'pending' ? 'Pending' : 'Unsafe';
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
        showToast('Google Drive 連結成功！');
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
            safetyBadge.textContent = '可公開 (Safe)';
            safetyBadge.className = 'status-badge status-safe';
        } else if (decision === 'pending') {
            safetyBadge.textContent = '待人員判定 (Pending)';
            safetyBadge.className = 'status-badge status-pending';
        } else {
            safetyBadge.textContent = '不可公開 (Unsafe)';
            safetyBadge.className = 'status-badge status-unsafe';
        }

        const overrideEl = document.getElementById('override-indicator');
        if (currentData.user_decision !== currentData.ai_decision) {
            if (!overrideEl) {
                const badge = document.createElement('span');
                badge.id = 'override-indicator';
                badge.className = 'override-badge';
                badge.textContent = '🔄 已覆寫';
                safetyBadge.parentElement.appendChild(badge);
            }
        } else {
            if (overrideEl) overrideEl.remove();
        }

        const toastText = decision === 'safe' ? '✅ Safe' : decision === 'pending' ? '⏳ Pending' : '❌ Unsafe';
        showToast(`已將此圖設為 ${toastText}`);
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
            const badgeText = decision === 'safe' ? 'Safe' : decision === 'pending' ? 'Pending' : 'Unsafe';

            html += `<div class="review-item${currentClass}" data-idx="${idx}">
                <span class="review-item-index">#${idx + 1}</span>
                <span class="review-item-name" title="${fileName}">${fileName}</span>
                ${isOverride ? '<span class="review-item-override">🔄</span>' : ''}
                <span class="review-item-badge ${badgeClass}">${badgeText}</span>
            </div>`;
        });

        reviewList.innerHTML = html;
        reviewSafeCount.textContent = `Safe: ${safeC}`;
        reviewPendingCount.textContent = `Pending: ${pendingC}`;
        reviewUnsafeCount.textContent = `Unsafe: ${unsafeC}`;

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
            showToast('沒有可歸檔的結果', 'error');
            return;
        }

        function setBusy(busy) {
            [document.getElementById('overview-finalize-btn'), finalizeBtn].forEach(btn => {
                if (!btn) return;
                btn.disabled = busy;
                btn.textContent = busy ? '⏳ 處理中...' : '✅ 確認批次辨識結果';
            });
        }

        if (batchMode === 'local') {
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
                    if (!res.ok) throw new Error('歸檔失敗');
                    const data = await res.json();
                    showToast(data.message);
                } catch (e) {
                    showToast(e.message, 'error');
                } finally {
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
                try {
                    const res = await fetch('/finalize_review/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ decisions, target_folder_id: targetId })
                    });
                    if (!res.ok) {
                        const err = await res.json();
                        throw new Error(err.detail || '歸檔失敗');
                    }
                    const data = await res.json();
                    showToast(`✅ ${data.message}`);
                } catch (e) {
                    showToast(e.message, 'error');
                } finally {
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
            organizeBtn.disabled = false;
            organizeBtn.textContent = '複製檔案並歸檔';
        }
    });

    // === Google Picker Integration ===
    let pickerApiLoaded = false;
    let oauthToken = null;
    let config = null;

    // Fetch config on load
    async function fetchConfig() {
        try {
            const res = await fetch('/api/config');
            config = await res.json();
            console.log("Config loaded:", !!config.google_client_id);
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

        // 若已有 token（由伺服器端 OAuth 同步或先前 Picker 授權取得），直接開啟 Picker
        if (oauthToken) {
            createPicker(targetId);
            return;
        }

        const tokenClient = google.accounts.oauth2.initTokenClient({
            client_id: config.google_client_id,
            scope: 'https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file',
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
        if (pickerApiLoaded && oauthToken) {
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
        }
    }

    function pickerCallback(data, targetId) {
        if (data.action === google.picker.Action.PICKED) {
            const folder = data.docs[0];
            const input = document.getElementById(targetId);
            if (input) {
                input.value = folder.id;
                showToast(`已選取資料夾：${folder.name}`);
            }
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

});
