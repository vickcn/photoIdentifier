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

    // Stats Elements
    const safetyBadge = document.getElementById('safety-badge');
    const fileNameDisplay = document.getElementById('file-name-display');
    const moderationReason = document.getElementById('moderation-reason');
    const faceCount = document.getElementById('face-count');
    const strapCount = document.getElementById('strap-count');
    const strapColor = document.getElementById('strap-color');

    // Toast
    const toastEl = document.getElementById('toast');

    // State
    let currentBatchResults = [];
    let currentIndex = 0;

    // === Helpers ===
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


    // === Batch Mode Handling ===
    analyzeBatchBtn.addEventListener('click', async () => {
        const source = document.querySelector('input[name="batch-source"]:checked').value;
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
            endpoint = '/batch_drive/';
            body = {
                folder_id: fId,
                target_folder_id: tId || null,
                concurrency: currentConcurrency
            };
        }

        showLoading(true);
        document.getElementById('loading-text').textContent = '正在批量辨識中，請稍候...';

        try {
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || '批量辨識失敗');
            }
            
            const data = await res.json();
            currentBatchResults = data.results.filter(r => r.status === 'ok'); 
            
            if (currentBatchResults.length === 0) {
                showToast('該資料夾內沒有成功辨識的圖片', 'error');
                return;
            }

            showToast(`批量完成！成功：${data.success}，失敗：${data.failed}`);
            
            // 雲端模式下，分類整理按鈕目前無效（已在雲端處理），可以隱藏
            if (source === 'local') {
                organizeArea.classList.remove('hidden');
            } else {
                organizeArea.classList.add('hidden');
            }
            
            currentIndex = 0;
            renderBatchViewer();

        } catch (e) {
            showToast(e.message, 'error');
        } finally {
            showLoading(false);
            document.getElementById('loading-text').textContent = '正在用 AI 魔法深度辨識中...';
        }
    });

    function renderBatchViewer() {
        if (currentBatchResults.length === 0) return;
        
        const currentData = currentBatchResults[currentIndex];
        
        emptyState.classList.add('hidden');
        splitViewer.classList.remove('hidden');

        pageIndicator.textContent = `${currentIndex + 1} / ${currentBatchResults.length}`;
        
        if (currentData.output_b64) {
            // Google Drive Mode: use base64 (since we don't have a local path)
            // Note: In real app, we might want to fetch original from drive too, 
            // for now, let's assume original is also provided or we use a placeholder
            originalImg.src = 'https://placehold.co/600x400?text=Origin+ID:+' + currentData.drive_id;
            annotatedImg.src = 'data:image/jpeg;base64,' + currentData.output_b64;
        } else {
            // Local Mode
            originalImg.src = `/local_file/?path=${encodeURIComponent(currentData.original_path)}`;
            annotatedImg.src = `/local_file/?path=${encodeURIComponent(currentData.output)}`;
        }

        // Fake analysis object for UI function
        const fakeAnalysis = {
            is_safe_for_public: currentData.is_safe_for_public,
            moderation_reason: currentData.moderation_reason,
            face_bboxes: new Array(currentData.face_count),
            strap_bboxes: currentData.has_brand_strap ? [1] : [],
            strap_color: currentData.strap_color
        };
        updateStatsUI(currentData.file, fakeAnalysis);
    }

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
        }
    });

    nextBtn.addEventListener('click', () => {
        if (currentBatchResults.length > 0) {
            currentIndex = (currentIndex + 1) % currentBatchResults.length;
            renderBatchViewer();
        }
    });

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

});
