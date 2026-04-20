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
    const outputFolder = document.getElementById('batch-output-folder');
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

    // Review Elements
    const decisionButtons = document.getElementById('decision-buttons');
    const btnSetSafe = document.getElementById('btn-set-safe');
    const btnSetUnsafe = document.getElementById('btn-set-unsafe');
    const reviewSummary = document.getElementById('review-summary');
    const reviewList = document.getElementById('review-list');
    const reviewSafeCount = document.getElementById('review-safe-count');
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
    window.__exitFullscreen   = () => toggleFullscreen(false);

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
            
            // Reset viewer
            splitViewer.classList.add('hidden');
            emptyState.classList.remove('hidden');
            currentBatchResults = [];
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
        
        if (analysis.is_safe_for_public) {
            safetyBadge.textContent = '可公開 (Safe)';
            safetyBadge.className = 'status-badge status-safe';
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
        
        let endpoint = '/batch/';
        let body = {};

        if (source === 'local') {
            const inputDir = inputFolder.value.trim();
            const outputDir = outputFolder.value.trim();
            if (!inputDir || !outputDir) {
                showToast('請填寫來源與輸出資料夾路徑', 'error');
                return;
            }
            body = {
                input_folder: inputDir,
                output_folder: outputDir,
                concurrency: currentConcurrency
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
                concurrency: currentConcurrency
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
                            successCount++;
                            totalImages = data.total;
                            const aiSafe = data.result ? data.result.is_safe_for_public : data.is_safe_for_public;
                            data.user_decision = aiSafe ? 'safe' : 'unsafe';
                            data.ai_decision = data.user_decision;
                            currentBatchResults.push(data);
                        } else if (data.status === 'error') {
                            failedCount++;
                            totalImages = data.total || totalImages;
                            showToast(`${data.file_name} 辨識出錯`, 'error');
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
            } else {
                organizeArea.classList.add('hidden');
            }

            if (currentBatchResults.length > 0) {
                showBatchOverview();
            }

        } catch (e) {
            showToast(e.message, 'error');
        } finally {
            showLoading(false);
            document.getElementById('loading-text').textContent = '正在用 AI 魔法深度辨識中...';
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
        let safeC = 0, unsafeC = 0;
        currentBatchResults.forEach(r => {
            if (r.user_decision === 'safe') safeC++; else unsafeC++;
        });
        document.getElementById('ov-total').textContent = currentBatchResults.length;
        document.getElementById('ov-safe').textContent = safeC;
        document.getElementById('ov-unsafe').textContent = unsafeC;

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
            const isSafe = item.user_decision === 'safe';
            const isOverride = item.user_decision !== item.ai_decision;
            const fileName = item.file_name || item.file || `圖片 ${idx + 1}`;
            const src = getItemImgSrc(item);
            html += `<div class="thumbnail-item" data-idx="${idx}" title="${fileName}">
                <img src="${src}" alt="${fileName}" loading="lazy">
                <div class="thumbnail-overlay">
                    <span class="thumb-badge ${isSafe ? 'safe' : 'unsafe'}">${isSafe ? 'Safe' : 'Unsafe'}</span>
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
            const isSafe = item.user_decision === 'safe';
            const isOverride = item.user_decision !== item.ai_decision;
            const fileName = item.file_name || item.file || `圖片 ${idx + 1}`;
            const src = getItemImgSrc(item);
            html += `<div class="overview-list-row" data-idx="${idx}">
                <span class="list-row-num">#${idx + 1}</span>
                <img class="list-row-thumb" src="${src}" alt="${fileName}" loading="lazy">
                <span class="list-row-name" title="${fileName}">${fileName}</span>
                ${isOverride ? '<span class="list-row-override">🔄</span>' : ''}
                <span class="list-row-badge ${isSafe ? 'safe' : 'unsafe'}">${isSafe ? 'Safe' : 'Unsafe'}</span>
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
        emptyState.classList.add('hidden');
        splitViewer.classList.remove('hidden');
        decisionButtons.style.display = 'flex';
        document.getElementById('back-to-overview-btn').classList.remove('hidden');
        renderBatchViewer();
    }

    window.__backToOverview = function() {
        batchOverviewActive = true;
        splitViewer.classList.add('hidden');
        decisionButtons.style.display = 'none';
        document.getElementById('back-to-overview-btn').classList.add('hidden');
        document.getElementById('batch-overview').classList.remove('hidden');
        renderBatchOverview();
    };

    window.__setOverviewMode = function(mode) {
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

        // Reset button states
        btnSetSafe.classList.remove('active-safe');
        btnSetUnsafe.classList.remove('active-unsafe');

        if (currentData.user_decision === 'safe') {
            btnSetSafe.classList.add('active-safe');
        } else if (currentData.user_decision === 'unsafe') {
            btnSetUnsafe.classList.add('active-unsafe');
        }
    }

    window.__setDecision = function(decision) {
        if (currentBatchResults.length === 0) return;
        const currentData = currentBatchResults[currentIndex];
        if (!currentData) return;

        currentData.user_decision = decision;
        renderDecisionButtons();

        // 更新 safety badge 顯示
        if (decision === 'safe') {
            safetyBadge.textContent = '可公開 (Safe)';
            safetyBadge.className = 'status-badge status-safe';
        } else {
            safetyBadge.textContent = '不可公開 (Unsafe)';
            safetyBadge.className = 'status-badge status-unsafe';
        }

        // 顯示覆寫標記
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

        showToast(`已將此圖設為 ${decision === 'safe' ? '✅ Safe' : '❌ Unsafe'}`);
    };

    function renderReviewSummary() {
        if (currentBatchResults.length === 0) return;

        let safeC = 0, unsafeC = 0;
        let html = '';

        currentBatchResults.forEach((item, idx) => {
            const isSafe = item.user_decision === 'safe';
            const isOverride = item.user_decision !== item.ai_decision;
            if (isSafe) safeC++; else unsafeC++;

            const fileName = item.file_name || item.file || `圖片 ${idx + 1}`;
            const currentClass = idx === currentIndex ? ' current' : '';

            html += `<div class="review-item${currentClass}" data-idx="${idx}">
                <span class="review-item-index">#${idx + 1}</span>
                <span class="review-item-name" title="${fileName}">${fileName}</span>
                ${isOverride ? '<span class="review-item-override">🔄</span>' : ''}
                <span class="review-item-badge ${isSafe ? 'safe' : 'unsafe'}">${isSafe ? 'Safe' : 'Unsafe'}</span>
            </div>`;
        });

        reviewList.innerHTML = html;
        reviewSafeCount.textContent = `Safe: ${safeC}`;
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

    window.__finalizeReview = async function() {
        if (currentBatchResults.length === 0) {
            showToast('沒有可歸檔的結果', 'error');
            return;
        }

        const activeBtn = document.getElementById('overview-finalize-btn') || finalizeBtn;

        function setBusy(busy) {
            [document.getElementById('overview-finalize-btn'), finalizeBtn].forEach(btn => {
                if (!btn) return;
                btn.disabled = busy;
                btn.textContent = busy ? '⏳ 歸檔中...' : '🚀 確認並批次歸檔';
            });
        }

        if (batchMode === 'local') {
            const safe = safeFolder.value.trim();
            const unsafe = unsafeFolder.value.trim();
            if (!safe || !unsafe) {
                showToast('請填寫安全與不安全的分流資料夾路徑', 'error');
                return;
            }

            const adjusted = currentBatchResults.map(r => ({
                ...r,
                is_safe_for_public: r.user_decision === 'safe'
            }));

            setBusy(true);
            try {
                const res = await fetch('/organize_batch/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ results: adjusted, safe_folder: safe, unsafe_folder: unsafe })
                });
                if (!res.ok) throw new Error('歸檔失敗');
                const data = await res.json();
                showToast(data.message);
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                setBusy(false);
            }
        } else {
            const targetId = driveTargetId.value.trim();
            if (!targetId) {
                showToast('請填寫 Drive 目標資料夾 ID 才能歸檔', 'error');
                return;
            }

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

            if(!res.ok) throw new Error('分類複製失敗');
            const data = await res.json();
            
            if(data.errors && data.errors.length > 0) {
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

        if (oauthToken === null) {
            tokenClient.requestAccessToken({ prompt: 'consent' });
        } else {
            tokenClient.requestAccessToken({ prompt: '' });
        }
    }

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
});
