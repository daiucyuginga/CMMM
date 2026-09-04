/**
 * CMMM - フロントエンド アプリケーションロジック
 */

(function() {
  "use strict";

  // --- 状態管理 ---
  const state = {
    mode: "lossless", // "lossless" または "smart"
    baseMeta: null,   // 1本目の動画メタデータ (互換性基準)
    clips: [],        // クリップ一覧
    selectedClipId: null,
    isPlaying: false,
    bridge: null,
    draggedClipIndex: null,
    isDraggingHandle: null // "in" または "out"
  };

  // --- DOM要素 ---
  const elements = {
    video: document.getElementById("videoPlayer"),
    placeholder: document.getElementById("videoPlaceholder"),
    btnAddVideo: document.getElementById("btnAddVideo"),
    btnSaveRecipe: document.getElementById("btnSaveRecipe"),
    btnExport: document.getElementById("btnExport"),
    modeToggle: document.getElementById("modeToggle"),
    storyboardList: document.getElementById("storyboardList"),
    currentTime: document.getElementById("currentTime"),
    clipDuration: document.getElementById("clipDuration"),
    rangeIn: document.getElementById("rangeIn"),
    rangeOut: document.getElementById("rangeOut"),
    seekContainer: document.getElementById("seekContainer"),
    rangeHighlight: document.getElementById("rangeHighlight"),
    handleIn: document.getElementById("handleIn"),
    handleOut: document.getElementById("handleOut"),
    playhead: document.getElementById("playhead"),
    btnPlayPause: document.getElementById("btnPlayPause"),
    btnStepBack: document.getElementById("btnStepBack"),
    btnStepForward: document.getElementById("btnStepForward"),
    btnPrevKeyframe: document.getElementById("btnPrevKeyframe"),
    btnNextKeyframe: document.getElementById("btnNextKeyframe"),
    btnSetIn: document.getElementById("btnSetIn"),
    btnSetOut: document.getElementById("btnSetOut"),
    btnSplit: document.getElementById("btnSplit"),
    btnResetRange: document.getElementById("btnResetRange"),
    dropOverlay: document.getElementById("dropOverlay"),
    exportModal: document.getElementById("exportModal"),
    exportProgressBar: document.getElementById("exportProgressBar"),
    exportProgressText: document.getElementById("exportProgressText"),
    btnCancelExport: document.getElementById("btnCancelExport"),
    toastContainer: document.getElementById("toastContainer"),
    // サイドバー情報
    baseFileName: document.getElementById("baseFileName"),
    baseResolution: document.getElementById("baseResolution"),
    baseVideoCodec: document.getElementById("baseVideoCodec"),
    baseAudioCodec: document.getElementById("baseAudioCodec"),
    totalClipsCount: document.getElementById("totalClipsCount"),
    totalDuration: document.getElementById("totalDuration"),
    selFileName: document.getElementById("selFileName"),
    selStart: document.getElementById("selStart"),
    selEnd: document.getElementById("selEnd"),
    selDuration: document.getElementById("selDuration"),
  };

  // --- QWebChannel 初期化 ---
  function initBridge() {
    if (typeof QWebChannel !== "undefined" && window.qt && window.qt.webChannelTransport) {
      new QWebChannel(window.qt.webChannelTransport, function(channel) {
        state.bridge = channel.objects.bridge;
        console.log("QWebChannel connected successfully.");
      });
    } else {
      console.warn("QWebChannel is not available (running in pure browser mode).");
    }
  }

  // --- ユーティリティ: 秒 ⇔ タイムコード変換 ---
  function formatTime(sec) {
    if (isNaN(sec) || sec < 0) sec = 0;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    const ms = Math.floor((sec % 1) * 1000);
    const pad = (n, len = 2) => String(n).padStart(len, "0");
    return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(ms, 3)}`;
  }

  function showToast(type, message) {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    const icon = type === "warning" ? "⚠️" : type === "error" ? "❌" : "✅";
    toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;
    elements.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateX(50px)";
      setTimeout(() => toast.remove(), 300);
    }, 4500);
  }

  // --- ファイル追加と互換性チェック ---
  async function handleAddFiles(filePaths) {
    if (!state.bridge) {
      showToast("error", "バックエンドブリッジに接続されていません。");
      return;
    }

    for (const p of filePaths) {
      // 1本目の場合: 基準メタデータを取得
      if (!state.baseMeta) {
        const resStr = await new Promise(resolve => state.bridge.getVideoInfo(p, resolve));
        const res = JSON.parse(resStr);
        if (!res.success) {
          showToast("error", `動画の解析に失敗しました: ${res.error}`);
          continue;
        }

        state.baseMeta = res.metadata;
        updateBaseInfoUI();
        addClip(p, res.metadata, res.keyframes);
        showToast("success", `基準動画を読み込みました: ${res.metadata.file_name}`);
      } else {
        // 2本目以降: 無劣化互換性チェック！
        const resStr = await new Promise(resolve =>
          state.bridge.checkCompatibility(p, JSON.stringify(state.baseMeta), resolve)
        );
        const res = JSON.parse(resStr);
        if (!res.success) {
          showToast("error", `解析エラー: ${res.error}`);
          continue;
        }

        if (!res.compatible) {
          // 形式不一致のため弾く！
          showToast("warning", `⚠️ 互換性不一致のため除外: ${res.reason}`);
          continue;
        }

        // 互換性OKなら追加
        const infoStr = await new Promise(resolve => state.bridge.getVideoInfo(p, resolve));
        const info = JSON.parse(infoStr);
        addClip(p, info.metadata, info.keyframes);
        showToast("success", `動画を追加しました: ${info.metadata.file_name}`);
      }
    }
  }

  // クリップオブジェクト作成＆追加
  async function addClip(filePath, meta, keyframes) {
    const clipId = "clip_" + Date.now() + "_" + Math.random().toString(36).substr(2, 5);
    const clip = {
      id: clipId,
      sourcePath: filePath,
      fileName: meta.file_name,
      fps: meta.fps || 30.0,
      start: 0.0,
      end: meta.duration,
      originalDuration: meta.duration,
      keyframes: keyframes || [0.0],
      thumbnail: ""
    };

    // サムネイル生成
    if (state.bridge) {
      const thumb = await new Promise(resolve =>
        state.bridge.generateThumbnail(filePath, 0.5, resolve)
      );
      clip.thumbnail = thumb;
    }

    state.clips.push(clip);
    renderStoryboard();
    updateSidebarStats();

    // 最初のクリップなら自動選択
    if (state.clips.length === 1) {
      selectClip(clipId);
    }
  }

  // --- ストーリーボード描画 (WMM風) ---
  function renderStoryboard() {
    elements.storyboardList.innerHTML = "";

    state.clips.forEach((clip, index) => {
      const card = document.createElement("div");
      card.className = "clip-card" + (clip.id === state.selectedClipId ? " selected" : "");
      card.draggable = true;
      card.dataset.index = index;
      card.dataset.id = clip.id;

      const dur = clip.end - clip.start;

      card.innerHTML = `
        <div class="clip-thumb-area">
          ${clip.thumbnail ? `<img src="${clip.thumbnail}" class="clip-thumb-img" alt="">` : `<div style="font-size:24px">🎬</div>`}
          <span class="clip-badge-idx">#${index + 1}</span>
          <span class="clip-badge-duration">${formatTime(dur).slice(3, -4)}</span>
        </div>
        <div class="clip-meta-area">
          <div class="clip-name" title="${clip.fileName}">${clip.fileName}</div>
          <div class="clip-times">${formatTime(clip.start).slice(3, -4)} - ${formatTime(clip.end).slice(3, -4)}</div>
        </div>
        <button class="clip-btn-del" title="クリップ削除">✕</button>
      `;

      // クリックで選択
      card.addEventListener("click", (e) => {
        if (e.target.classList.contains("clip-btn-del")) return;
        selectClip(clip.id);
      });

      // 削除ボタン
      const delBtn = card.querySelector(".clip-btn-del");
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeClip(clip.id);
      });

      // ドラッグ＆ドロップ並び替えイベント
      card.addEventListener("dragstart", (e) => {
        state.draggedClipIndex = index;
        card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
      });

      card.addEventListener("dragend", () => {
        card.classList.remove("dragging");
        document.querySelectorAll(".clip-card").forEach(c => c.classList.remove("drag-over"));
        state.draggedClipIndex = null;
      });

      card.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        card.classList.add("drag-over");
      });

      card.addEventListener("dragleave", () => {
        card.classList.remove("drag-over");
      });

      card.addEventListener("drop", (e) => {
        e.preventDefault();
        card.classList.remove("drag-over");
        const targetIndex = index;
        if (state.draggedClipIndex !== null && state.draggedClipIndex !== targetIndex) {
          // 配列内の順序を入れ替え
          const item = state.clips.splice(state.draggedClipIndex, 1)[0];
          state.clips.splice(targetIndex, 0, item);
          renderStoryboard();
          updateSidebarStats();
        }
      });

      elements.storyboardList.appendChild(card);
    });
  }

  // クリップ削除
  function removeClip(clipId) {
    const idx = state.clips.findIndex(c => c.id === clipId);
    if (idx === -1) return;
    state.clips.splice(idx, 1);

    if (state.selectedClipId === clipId) {
      if (state.clips.length > 0) {
        selectClip(state.clips[Math.max(0, idx - 1)].id);
      } else {
        state.selectedClipId = null;
        state.baseMeta = null;
        elements.video.src = "";
        elements.videoPlaceholder.style.display = "flex";
        updateBaseInfoUI();
        updateSelectedClipUI();
      }
    }
    renderStoryboard();
    updateSidebarStats();
  }

  // クリップ選択
  function selectClip(clipId) {
    const clip = state.clips.find(c => c.id === clipId);
    if (!clip) return;

    state.selectedClipId = clipId;
    elements.videoPlaceholder.style.display = "none";

    // 動画プレイヤーにセット (RangeストリーミングURL)
    const streamUrl = `/video?path=${encodeURIComponent(clip.sourcePath)}`;
    if (elements.video.dataset.currentSrc !== clip.sourcePath) {
      elements.video.src = streamUrl;
      elements.video.dataset.currentSrc = clip.sourcePath;
    }

    elements.video.currentTime = clip.start;
    updateSeekUI();
    updateSelectedClipUI();
    renderStoryboard();
  }

  // --- シークバー & In/Out トリミング ---
  function updateSeekUI() {
    const clip = getSelectedClip();
    if (!clip) return;

    const total = clip.originalDuration || 1;
    const cur = elements.video.currentTime;

    // 時間表示
    elements.currentTime.textContent = formatTime(cur);
    elements.clipDuration.textContent = formatTime(total);
    elements.rangeIn.textContent = formatTime(clip.start);
    elements.rangeOut.textContent = formatTime(clip.end);

    // In/Out ハンドル位置とハイライト
    const inPercent = (clip.start / total) * 100;
    const outPercent = (clip.end / total) * 100;
    const playPercent = (cur / total) * 100;

    elements.handleIn.style.left = `${inPercent}%`;
    elements.handleOut.style.left = `${outPercent}%`;
    elements.rangeHighlight.style.left = `${inPercent}%`;
    elements.rangeHighlight.style.width = `${Math.max(0, outPercent - inPercent)}%`;
    elements.playhead.style.left = `${playPercent}%`;
  }

  function getSelectedClip() {
    return state.clips.find(c => c.id === state.selectedClipId);
  }

  // シーククリック
  elements.seekContainer.addEventListener("mousedown", (e) => {
    const clip = getSelectedClip();
    if (!clip) return;

    const rect = elements.seekContainer.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, x / rect.width));
    const targetTime = ratio * clip.originalDuration;

    // ハンドルクリックの判定
    const inX = (clip.start / clip.originalDuration) * rect.width;
    const outX = (clip.end / clip.originalDuration) * rect.width;

    if (Math.abs(x - inX) < 10) {
      state.isDraggingHandle = "in";
    } else if (Math.abs(x - outX) < 10) {
      state.isDraggingHandle = "out";
    } else {
      elements.video.currentTime = targetTime;
      updateSeekUI();
    }
  });

  window.addEventListener("mousemove", (e) => {
    if (!state.isDraggingHandle) return;
    const clip = getSelectedClip();
    if (!clip) return;

    const rect = elements.seekContainer.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, x / rect.width));
    const targetTime = ratio * clip.originalDuration;

    if (state.isDraggingHandle === "in") {
      clip.start = Math.max(0, Math.min(targetTime, clip.end - 0.1));
    } else if (state.isDraggingHandle === "out") {
      clip.end = Math.min(clip.originalDuration, Math.max(targetTime, clip.start + 0.1));
    }
    updateSeekUI();
    updateSelectedClipUI();
    renderStoryboard();
    updateSidebarStats();
  });

  window.addEventListener("mouseup", () => {
    state.isDraggingHandle = null;
  });

  // 再生時間の更新イベント
  elements.video.addEventListener("timeupdate", () => {
    const clip = getSelectedClip();
    if (!clip) return;

    // クリップ末尾に到達したら一時停止
    if (elements.video.currentTime >= clip.end && !elements.video.paused) {
      elements.video.pause();
      elements.video.currentTime = clip.start;
      state.isPlaying = false;
      elements.btnPlayPause.textContent = "▶";
    }
    updateSeekUI();
  });

  // 再生 / 一時停止
  function togglePlay() {
    if (elements.video.paused) {
      elements.video.play();
      state.isPlaying = true;
      elements.btnPlayPause.textContent = "⏸";
    } else {
      elements.video.pause();
      state.isPlaying = false;
      elements.btnPlayPause.textContent = "▶";
    }
  }

  elements.btnPlayPause.addEventListener("click", togglePlay);

  // コマ送り / コマ戻し
  elements.btnStepBack.addEventListener("click", () => {
    const clip = getSelectedClip();
    const step = clip ? 1.0 / clip.fps : 1.0 / 30.0;
    elements.video.currentTime = Math.max(0, elements.video.currentTime - step);
  });

  elements.btnStepForward.addEventListener("click", () => {
    const clip = getSelectedClip();
    const step = clip ? 1.0 / clip.fps : 1.0 / 30.0;
    elements.video.currentTime = Math.min(elements.video.duration || 99999, elements.video.currentTime + step);
  });

  // キーフレームジャンプ
  elements.btnPrevKeyframe.addEventListener("click", () => {
    const clip = getSelectedClip();
    if (!clip || !clip.keyframes) return;
    const cur = elements.video.currentTime;
    const prevKfs = clip.keyframes.filter(k => k < cur - 0.05);
    if (prevKfs.length > 0) {
      elements.video.currentTime = prevKfs[prevKfs.length - 1];
    }
  });

  elements.btnNextKeyframe.addEventListener("click", () => {
    const clip = getSelectedClip();
    if (!clip || !clip.keyframes) return;
    const cur = elements.video.currentTime;
    const nextKf = clip.keyframes.find(k => k > cur + 0.05);
    if (nextKf !== undefined) {
      elements.video.currentTime = nextKf;
    }
  });

  // In点 / Out点 設定
  elements.btnSetIn.addEventListener("click", () => {
    const clip = getSelectedClip();
    if (!clip) return;
    const cur = elements.video.currentTime;
    clip.start = Math.min(cur, clip.end - 0.1);
    updateSeekUI();
    updateSelectedClipUI();
    renderStoryboard();
    updateSidebarStats();
    showToast("success", `In点を設定: ${formatTime(clip.start)}`);
  });

  elements.btnSetOut.addEventListener("click", () => {
    const clip = getSelectedClip();
    if (!clip) return;
    const cur = elements.video.currentTime;
    clip.end = Math.max(cur, clip.start + 0.1);
    updateSeekUI();
    updateSelectedClipUI();
    renderStoryboard();
    updateSidebarStats();
    showToast("success", `Out点を設定: ${formatTime(clip.end)}`);
  });

  elements.btnResetRange.addEventListener("click", () => {
    const clip = getSelectedClip();
    if (!clip) return;
    clip.start = 0.0;
    clip.end = clip.originalDuration;
    updateSeekUI();
    updateSelectedClipUI();
    renderStoryboard();
    updateSidebarStats();
    showToast("success", "クリップ範囲をリセットしました");
  });

  // クリップ分割 (Split)
  elements.btnSplit.addEventListener("click", splitCurrentClip);

  function splitCurrentClip() {
    const clip = getSelectedClip();
    if (!clip) return;

    const cur = elements.video.currentTime;
    if (cur <= clip.start + 0.1 || cur >= clip.end - 0.1) {
      showToast("warning", "分割位置がクリップの端に近すぎます");
      return;
    }

    const idx = state.clips.findIndex(c => c.id === clip.id);
    const originalEnd = clip.end;

    // クリップA (前半分)
    clip.end = cur;

    // クリップB (後半分)
    const newId = "clip_" + Date.now() + "_" + Math.random().toString(36).substr(2, 5);
    const newClip = {
      ...clip,
      id: newId,
      start: cur,
      end: originalEnd
    };

    state.clips.splice(idx + 1, 0, newClip);
    renderStoryboard();
    selectClip(newId);
    updateSidebarStats();
    showToast("success", `✂ クリップを分割しました: ${formatTime(cur)}`);
  }

  // --- サイドバー情報更新 ---
  function updateBaseInfoUI() {
    if (!state.baseMeta) {
      elements.baseFileName.textContent = "-";
      elements.baseResolution.textContent = "-";
      elements.baseVideoCodec.textContent = "-";
      elements.baseAudioCodec.textContent = "-";
      return;
    }
    elements.baseFileName.textContent = state.baseMeta.file_name;
    elements.baseResolution.textContent = `${state.baseMeta.width} x ${state.baseMeta.height} (${state.baseMeta.fps} fps)`;
    elements.baseVideoCodec.textContent = `${state.baseMeta.video_codec} (${state.baseMeta.video_pix_fmt})`;
    elements.baseAudioCodec.textContent = state.baseMeta.audio_codec ? `${state.baseMeta.audio_codec} (${state.baseMeta.audio_sample_rate}Hz)` : "なし";
  }

  function updateSelectedClipUI() {
    const clip = getSelectedClip();
    if (!clip) {
      elements.selFileName.textContent = "-";
      elements.selStart.textContent = "-";
      elements.selEnd.textContent = "-";
      elements.selDuration.textContent = "-";
      return;
    }
    elements.selFileName.textContent = clip.fileName;
    elements.selStart.textContent = formatTime(clip.start);
    elements.selEnd.textContent = formatTime(clip.end);
    elements.selDuration.textContent = formatTime(clip.end - clip.start);
  }

  function updateSidebarStats() {
    elements.totalClipsCount.textContent = state.clips.length;
    const totalSec = state.clips.reduce((acc, c) => acc + (c.end - c.start), 0);
    elements.totalDuration.textContent = formatTime(totalSec).slice(0, -4);
  }

  // --- モードトグル ---
  elements.modeToggle.addEventListener("click", (e) => {
    const opt = e.target.closest(".toggle-option");
    if (!opt) return;

    document.querySelectorAll(".toggle-option").forEach(o => o.classList.remove("active"));
    opt.classList.add("active");
    state.mode = opt.dataset.mode;
    showToast("success", `モード変更: ${state.mode === "lossless" ? "⚡ 完全無劣化 (Keyframe)" : "🎯 スマートカット (Frame-Accurate)"}`);
  });

  // --- ファイルドロップのグローバルハンドリング ---
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    elements.dropOverlay.classList.add("active");
  });

  elements.dropOverlay.addEventListener("dragover", (e) => {
    e.preventDefault();
  });

  elements.dropOverlay.addEventListener("dragleave", (e) => {
    if (e.relatedTarget === null) {
      elements.dropOverlay.classList.remove("active");
    }
  });

  elements.dropOverlay.addEventListener("drop", (e) => {
    e.preventDefault();
    elements.dropOverlay.classList.remove("active");
  });

  // Python側 (PySide6) からドロップされたファイルパスを受け取るコールバック
  window.onNativeFilesDropped = function(filePaths) {
    elements.dropOverlay.classList.remove("active");
    handleAddFiles(filePaths);
  };

  // 動画追加ボタン (ダイアログ経由)
  elements.btnAddVideo.addEventListener("click", async () => {
    if (!state.bridge) return;
    const resStr = await new Promise(resolve => state.bridge.openFileDialog(resolve));
    const files = JSON.parse(resStr);
    if (files && files.length > 0) {
      handleAddFiles(files);
    }
  });

  // --- レシピ保存 ---
  elements.btnSaveRecipe.addEventListener("click", async () => {
    if (!state.bridge || state.clips.length === 0) {
      showToast("warning", "保存するクリップがありません");
      return;
    }

    const savePath = await new Promise(resolve =>
      state.bridge.saveFileDialog("cmmm_recipe.json", resolve)
    );
    if (!savePath) return;

    const recipe = {
      mode: state.mode,
      output_file: "output.mp4",
      clips: state.clips.map(c => ({
        source: c.sourcePath,
        start: c.start,
        end: c.end
      }))
    };

    const ok = await new Promise(resolve =>
      state.bridge.saveRecipe(JSON.stringify(recipe), savePath, resolve)
    );
    if (ok) {
      showToast("success", `レシピを保存しました: ${savePath}`);
    } else {
      showToast("error", "レシピ保存に失敗しました");
    }
  });

  // --- エクスポート実行 ---
  elements.btnExport.addEventListener("click", async () => {
    if (!state.bridge || state.clips.length === 0) {
      showToast("warning", "エクスポートするクリップがありません");
      return;
    }

    const savePath = await new Promise(resolve =>
      state.bridge.saveFileDialog("output_cmmm.mp4", resolve)
    );
    if (!savePath) return;

    const recipe = {
      mode: state.mode,
      output_file: savePath,
      clips: state.clips.map(c => ({
        source: c.sourcePath,
        start: c.start,
        end: c.end
      }))
    };

    // モーダル表示
    elements.exportModal.classList.add("active");
    elements.exportProgressBar.style.width = "0%";
    elements.exportProgressText.textContent = "処理を開始しています...";
    elements.btnCancelExport.style.display = "none";

    // 実行呼び出し
    state.bridge.runExport(JSON.stringify(recipe));
  });

  // 進捗コールバック
  window.onExportProgress = function(current, total, message) {
    const percent = total > 0 ? Math.floor((current / total) * 100) : 0;
    elements.exportProgressBar.style.width = `${percent}%`;
    elements.exportProgressText.textContent = `[${percent}%] ${message}`;
  };

  // 完了コールバック
  window.onExportFinished = function(success, message) {
    elements.exportProgressBar.style.width = success ? "100%" : "0%";
    elements.exportProgressText.textContent = message;
    elements.btnCancelExport.style.display = "block";
    showToast(success ? "success" : "error", message);
  };

  elements.btnCancelExport.addEventListener("click", () => {
    elements.exportModal.classList.remove("active");
  });

  // --- ショートカットキー ---
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;

    if (e.code === "Space") {
      e.preventDefault();
      togglePlay();
    } else if (e.code === "KeyS" && !e.ctrlKey) {
      e.preventDefault();
      splitCurrentClip();
    } else if (e.code === "KeyI") {
      e.preventDefault();
      elements.btnSetIn.click();
    } else if (e.code === "KeyO") {
      e.preventDefault();
      elements.btnSetOut.click();
    } else if (e.code === "Delete" || e.code === "Backspace") {
      if (state.selectedClipId) {
        removeClip(state.selectedClipId);
      }
    } else if (e.code === "ArrowLeft") {
      e.preventDefault();
      if (e.shiftKey) elements.btnPrevKeyframe.click();
      else elements.btnStepBack.click();
    } else if (e.code === "ArrowRight") {
      e.preventDefault();
      if (e.shiftKey) elements.btnNextKeyframe.click();
      else elements.btnStepForward.click();
    }
  });

  // 初期化実行
  window.addEventListener("DOMContentLoaded", initBridge);
})();
