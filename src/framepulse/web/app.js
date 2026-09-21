"use strict";

const $ = (id) => document.getElementById(id);
const state = {token: null, config: {}, assets: [], document: null, assetId: null, shotIndex: 0, keyframeId: null, jobs: [], jobStates: new Map(), submittedJobs: new Map(), dirty: false, busy: false, drawing: false, draftRegion: null, loadedAsset: null, loadingAsset: null, loadSequence: 0, libraryLoadSequence: 0, mediaGeneration: 0, seekSequence: 0, pendingSeek: null, online: false};
const visualFields = [
  ["shot_size", "景别", "例如：中近景"], ["camera_angle", "机位角度", "例如：低机位仰拍"],
  ["camera_movement", "摄影机运动", "例如：缓慢推近", true], ["composition", "构图与空间", "主体位置、纵深与视线", true],
  ["lighting", "光线", "方向、软硬、明暗关系", true], ["color", "色彩", "色调、色温与色彩关系", true]
];
const storyFields = [
  ["subjects", "主体", "人物、物体及其关系"], ["actions", "动作与调度", "主体行为与画面中的变化"],
  ["setting", "场景", "环境、时间与空间"], ["narrative_function", "叙事功能", "这个镜头如何推进表达"], ["tags", "检索标签", "以逗号分隔，例如：逆光, 人物, 城市"]
];
const emotionFields = [["tension", "张力"], ["valence", "情绪效价"], ["arousal", "唤醒度"]];
const originNames = {measured: "测量", inferred: "AI 推断", human: "人工"};
const reviewNames = {unreviewed: "未确认", accepted: "已确认", edited: "已编辑", rejected: "待重做"};
const jobNames = {queued: "排队中", running: "分析中", completed: "已完成", failed: "分析失败", cancelled: "已取消"};
let pollTimer, toastTimer, regionPointer, lastTimelineUpdate = 0;

function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
function currentShot() { return state.document?.shots?.[state.shotIndex] ?? null; }
function currentFrame() { const frames = currentShot()?.keyframes || []; return frames.find(frame => frame.id === state.keyframeId) || frames[0] || null; }
function formatTime(us = 0) { const ms = Math.max(0, Math.round(Number(us || 0) / 1000)), s = Math.floor(ms / 1000); return `${String(Math.floor(s / 60)).padStart(2,"0")}:${String(s % 60).padStart(2,"0")}.${String(ms % 1000).padStart(3,"0")}`; }
function durationLabel(us) { if (us == null) return "图片"; const sec = us / 1e6; return sec >= 60 ? `${Math.floor(sec / 60)} 分 ${Math.round(sec % 60)} 秒` : `${sec.toFixed(sec < 10 ? 1 : 0)} 秒`; }
function shortName(path) { return String(path || "素材分析").split(/[\\/]/).pop(); }
function shotNumber(index) { return String(index + 1).padStart(2,"0"); }
function frameUrl(frame) { return frame ? `/api/assets/${encodeURIComponent(state.assetId)}/files/${String(frame.uri).split("/").map(encodeURIComponent).join("/")}` : ""; }
function claimText(value) { if (value == null) return ""; if (Array.isArray(value) && value.every(item => typeof item === "string")) return value.join("、"); if (typeof value === "object") return JSON.stringify(value, null, 2); return String(value); }
function setOnline(online) { state.online = online; $("connection").className = `connection ${online ? "online" : "offline"}`; $("connection").lastElementChild.textContent = online ? "本地引擎就绪" : "引擎连接中断"; }
function showToast(message, error = false) { clearTimeout(toastTimer); $("toast").textContent = message; $("toast").className = `toast${error ? " error" : ""}`; $("toast").hidden = false; toastTimer = setTimeout(() => $("toast").hidden = true, error ? 7000 : 3800); }
async function api(url, options = {}) {
  const headers = {Accept: "application/json", ...options.headers};
  if (options.body != null) headers["Content-Type"] = "application/json";
  if (options.method && options.method !== "GET") { if (!state.token) throw new Error("本地服务尚未就绪，请稍后重试。"); headers["X-FramePulse-Token"] = state.token; }
  let response;
  try { response = await fetch(url, {...options, headers, cache: "no-store"}); } catch { setOnline(false); throw new Error("无法连接本地引擎。请确认帧脉服务仍在运行，然后刷新重试。"); }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" ? (data.detail || data.error || data.message) : data;
    const message = Array.isArray(detail) ? detail.map(item => item.msg || JSON.stringify(item)).join("；") : typeof detail === "object" ? JSON.stringify(detail) : detail;
    const error = new Error(response.status === 409 ? `记录暂时无法保存：${message || "内容已更新或正在分析，请刷新后重试。"}` : message || `请求失败 (${response.status})`);
    error.status = response.status; throw error;
  }
  setOnline(true); return data;
}

function renderLibrary() {
  $("assetCount").textContent = state.assets.length;
  const search = $("assetSearch").value.trim().toLocaleLowerCase();
  const assets = state.assets.filter(asset => String(asset.name || "").toLocaleLowerCase().includes(search));
  if (!assets.length) { $("assetList").innerHTML = `<p class="library-empty">${search ? "没有匹配的素材" : "还没有素材<br>从上方「导入素材」开始"}</p>`; return; }
  $("assetList").innerHTML = assets.map(asset => {
    const reviewed = asset.reviewed_count || 0, count = asset.shot_count || 0;
    const status = asset.status === "failed" ? "分析失败 · 可继续分析" : reviewed === count && count ? "全部已确认" : (asset.analyzed_count || 0) > 0 ? `${asset.analyzed_count} 镜有 AI 记录` : "测量完成 · 语义未标记";
    const warnings = (asset.warnings || []).map(warning => typeof warning === "string" ? warning : JSON.stringify(warning)).join("\n");
    const warningMarker = warnings ? `<span title="${escapeHtml(warnings)}" aria-label="注意事项：${escapeHtml(warnings)}"> ⓘ</span>` : "";
    const thumb = typeof asset.thumbnail === "string" && asset.thumbnail.startsWith("/") ? `<img class="asset-thumb" src="${escapeHtml(asset.thumbnail)}" alt="" loading="lazy">` : `<span class="asset-thumb" aria-hidden="true">${asset.kind === "image" ? "▧" : "▷"}</span>`;
    return `<button type="button" class="asset-card${asset.id === state.assetId ? " active" : ""}" data-asset="${escapeHtml(asset.id)}" title="${escapeHtml(asset.name)}" aria-pressed="${asset.id === state.assetId}">${thumb}<span class="asset-info"><strong>${escapeHtml(asset.name)}</strong><small>${asset.kind === "image" ? "图片" : durationLabel(asset.duration_us)} · ${count} ${asset.kind === "image" ? "画面" : "镜头"}</small><span class="asset-state">${escapeHtml(status)}${warningMarker}</span></span></button>`;
  }).join("");
}
async function loadAssets({autoSelect = false, refreshDocument = false} = {}) {
  const sequence = ++state.libraryLoadSequence, navigation = state.loadSequence;
  const data = await api("/api/assets"); if (sequence !== state.libraryLoadSequence) return;
  state.assets = data.assets || []; renderLibrary();
  if (navigation !== state.loadSequence || state.loadingAsset) return;
  if (autoSelect && !state.assetId && state.assets.length) await loadAsset(state.assets[0].id);
  else if (refreshDocument && state.assetId && !state.dirty && !state.busy) await loadAsset(state.assetId, {keepPosition: true});
}
function mayDiscard() { return !state.dirty || window.confirm("当前镜头有未保存的修改。放弃这些修改并切换？"); }
function updateLocation() {
  if (!state.assetId) return;
  const url = new URL(window.location.href); url.searchParams.set("asset", state.assetId);
  const shot = currentShot(); if (shot) url.searchParams.set("shot", shot.id); else url.searchParams.delete("shot");
  if (url.href !== window.location.href) window.history.replaceState(null, "", url);
}
function setAssetLoading(id) {
  state.loadingAsset = id; $("main").setAttribute("aria-busy", String(!!id)); $("inspectorForm").inert = !!id || state.busy;
}
async function loadAsset(id, {keepPosition = false, force = false, shotId = null} = {}) {
  if (state.busy) { showToast("正在保存镜头记录，请稍候"); return; }
  if (!force && !keepPosition && !mayDiscard()) return;
  const sequence = ++state.loadSequence; setAssetLoading(id);
  try {
    const doc = await api(`/api/assets/${encodeURIComponent(id)}`); if (sequence !== state.loadSequence) return;
    const previousShotId = currentShot()?.id;
    state.assetId = id; state.document = doc; state.dirty = false;
    const requestedShot = shotId ? doc.shots.findIndex(shot => shot.id === shotId) : -1;
    state.shotIndex = shotId ? Math.max(0, requestedShot) : keepPosition ? Math.max(0, doc.shots.findIndex(shot => shot.id === previousShotId)) : 0;
    state.keyframeId = currentShot()?.keyframes?.[0]?.id || null;
    stopDrawing(); renderAsset(!keepPosition || !!shotId); renderLibrary(); updateLocation();
    if (shotId && requestedShot < 0) showToast("链接中的镜头不存在，已打开该素材的第一个镜头。", true);
  } catch (error) { if (sequence === state.loadSequence) showToast(error.message, true); }
  finally { if (sequence === state.loadSequence) setAssetLoading(null); }
}
function renderAsset(resetPlayback = true) {
  const doc = state.document; if (!doc) return;
  const asset = doc.asset, tech = asset.technical || {}, shots = doc.shots || [];
  $("assetTitle").textContent = asset.name; $("assetTitle").title = asset.name;
  $("assetEyebrow").textContent = `${asset.kind === "image" ? "IMAGE" : "VIDEO"} / ${state.assetId.slice(0, 12).toUpperCase()}`;
  $("assetActions").hidden = false; $("welcome").hidden = true; $("mediaSurface").hidden = false;
  $("viewerLabel").textContent = asset.kind === "image" ? "图片预览" : "影像预览";
  $("viewerMeta").textContent = `${tech.width || "?"} × ${tech.height || "?"}${tech.codec ? ` · ${String(tech.codec).toUpperCase()}` : ""}`;
  $("mediaError").hidden = true;
  $("exportJson").href = `/api/assets/${encodeURIComponent(state.assetId)}/export?format=json`;
  $("exportCsv").href = `/api/assets/${encodeURIComponent(state.assetId)}/export?format=csv`;
  $("exportAnnotation").href = `/api/assets/${encodeURIComponent(state.assetId)}/export?format=web-annotation`;
  const isVideo = asset.kind === "video";
  $("video").hidden = !isVideo; $("image").hidden = isVideo;
  if (state.loadedAsset !== state.assetId) {
    state.mediaGeneration++; state.pendingSeek = null; state.loadedAsset = state.assetId;
    $("video").pause(); $("video").removeAttribute("src"); $("video").load();
    const mediaUrl = `/api/assets/${encodeURIComponent(state.assetId)}/media`;
    if (isVideo) { $("video").src = mediaUrl; $("video").load(); } else $("image").src = mediaUrl;
  }
  $("playButton").disabled = !isVideo; $("seek").disabled = !isVideo;
  $("seek").max = Math.max(0.001, (tech.duration_us || 0) / 1e6);
  $("duration").textContent = isVideo ? formatTime(tech.duration_us) : "静态图片";
  const playbackTime = isVideo ? state.pendingSeek?.time ?? $("video").currentTime : 0;
  $("currentTime").textContent = formatTime(playbackTime * 1e6); $("seek").value = playbackTime;
  $("shotCount").textContent = `${shots.length} ${isVideo ? "个镜头" : "个画面"}`;
  $("recordStatus").textContent = `帧脉记录 v${doc.spec_version} · 修订 ${doc.revision}`;
  const fps = tech.frame_rate?.denominator ? tech.frame_rate.numerator / tech.frame_rate.denominator : null;
  $("assetTech").textContent = `${tech.width || "?"} × ${tech.height || "?"}${fps ? ` · ${Number(fps.toFixed(3))} fps` : ""}${tech.has_audio ? " · 有音轨" : isVideo ? " · 无音轨" : ""}`;
  $("fullscreenButton").disabled = false;
  renderShotStrip(); renderInspector(); renderTimeline(); fitMedia();
  if (resetPlayback) seekToShot(false, {anchor:"start"}); else renderRegions();
}
function renderShotStrip() {
  const shots = state.document?.shots || [];
  if (!shots.length) { $("shotStrip").innerHTML = '<div class="strip-empty"><p>尚无镜头记录，尝试「继续分析」。</p></div>'; return; }
  $("shotStrip").innerHTML = shots.map((shot, index) => {
    const frame = shot.keyframes?.[0], title = claimText(shot.analysis?.summary?.value) || `镜头 ${shotNumber(index)}`;
    return `<button class="shot-card${index === state.shotIndex ? " active" : ""}" type="button" data-shot="${index}" aria-label="镜头 ${index + 1}，${escapeHtml(title)}" aria-pressed="${index === state.shotIndex}">${frame ? `<img class="shot-thumb" src="${escapeHtml(frameUrl(frame))}" alt="" loading="lazy">` : '<span class="shot-thumb"></span>'}<span class="shot-number">${shotNumber(index)}</span>${shot.review?.state === "accepted" ? '<span class="shot-check" title="已确认">✓</span>' : ""}<span class="shot-card-bottom"><span>${shot.range ? formatTime(shot.range.start_us) : "IMAGE"}</span><span>${shot.range ? `${((shot.range.end_us - shot.range.start_us) / 1e6).toFixed(2)}s` : "01 FRAME"}</span></span></button>`;
  }).join("");
}
function updateShotSelection(scroll = true) {
  $("shotStrip").querySelectorAll("[data-shot]").forEach(button => { const active = Number(button.dataset.shot) === state.shotIndex; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active)); if (active && scroll) button.scrollIntoView({block:"nearest",inline:"nearest",behavior:matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"}); });
  $("previousShot").disabled = state.shotIndex <= 0; $("nextShot").disabled = state.shotIndex >= (state.document?.shots?.length || 0) - 1;
}
function selectShot(index, {seek = true, scroll = true, ask = true} = {}) {
  if (state.busy) return;
  if (!state.document || index < 0 || index >= state.document.shots.length) return;
  if (index !== state.shotIndex && ask && !mayDiscard()) return;
  // Selecting a shot is a newer navigation intent than an in-flight asset fetch.
  if (state.loadingAsset && ask) { state.loadSequence++; setAssetLoading(null); }
  if (index !== state.shotIndex) { state.shotIndex = index; state.dirty = false; state.keyframeId = currentShot()?.keyframes?.[0]?.id || null; stopDrawing(); renderInspector(); }
  updateShotSelection(scroll); if (seek) seekToShot(false, {anchor:"start"}); renderTimeline(); renderRegions(); updateLocation();
}
function currentMediaMatches() {
  const video = $("video");
  return state.document?.asset.kind === "video" && state.loadedAsset === state.assetId && !!video.currentSrc && video.currentSrc === video.src;
}
function applyPendingSeek() {
  const pending = state.pendingSeek, video = $("video");
  if (!pending || pending.assetId !== state.assetId || pending.generation !== state.mediaGeneration || pending.source !== video.src || !currentMediaMatches() || video.readyState < 1) return;
  try {
    const time = Number.isFinite(video.duration) ? Math.min(pending.time, Math.max(0, video.duration)) : pending.time;
    video.currentTime = time;
    // A single target replaces previous requests; no per-seek metadata callbacks remain.
    if (state.pendingSeek?.sequence === pending.sequence) state.pendingSeek = null;
    $("currentTime").textContent = formatTime(time * 1e6); $("seek").value = time; updatePlayhead();
  } catch { /* Keep the latest target until the current resource is seekable. */ }
}
function requestSeek(time, pause = false) {
  if (state.document?.asset.kind !== "video") return;
  const video = $("video"); if (pause) video.pause();
  state.pendingSeek = {sequence:++state.seekSequence,assetId:state.assetId,generation:state.mediaGeneration,source:video.src,time:Math.max(0, Number(time) || 0)};
  $("currentTime").textContent = formatTime(state.pendingSeek.time * 1e6); $("seek").value = state.pendingSeek.time;
  applyPendingSeek();
}
function seekToShot(pause = false, {anchor = "frame"} = {}) {
  const shot = currentShot(), frame = currentFrame(); if (!shot) return;
  if (state.document.asset.kind === "video") {
    const time = (anchor === "start" ? shot.range?.start_us ?? 0 : frame?.time_us ?? shot.range?.start_us ?? 0) / 1e6;
    requestSeek(time, pause);
  }
  renderRegions();
}
function originMarkup(claim) {
  if (!claim || claim.value == null) return "未标记";
  const origin = claim.origin || "inferred", confidence = typeof claim.confidence === "number" ? ` · 自报置信 ${Math.round(claim.confidence * 100)}%` : "";
  return `<i class="origin ${escapeHtml(origin)}"></i><span title="${escapeHtml((originNames[origin] || origin) + confidence)}">${escapeHtml(originNames[origin] || origin)}</span>`;
}
function makeField([field, label, placeholder, wide], shot, multiline = false) {
  const claim = shot.analysis?.[field], value = claimText(claim?.value);
  return `<div class="field-block${wide ? " wide" : ""}"><label for="field-${field}">${label}<span>${originMarkup(claim)}</span></label>${multiline || wide ? `<textarea id="field-${field}" data-analysis="${field}" rows="${field === "narrative_function" ? 2 : 1}" placeholder="${escapeHtml(placeholder)}">${escapeHtml(value)}</textarea>` : `<input id="field-${field}" data-analysis="${field}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">`}</div>`;
}
function renderInspector() {
  const shot = currentShot(); $("inspectorEmpty").hidden = !!shot; $("inspectorForm").hidden = !shot;
  if (!shot) return;
  $("shotTitle").textContent = `SHOT ${shotNumber(state.shotIndex)}`;
  $("shotRange").textContent = shot.range ? `${formatTime(shot.range.start_us)} → ${formatTime(shot.range.end_us)}` : "STILL IMAGE";
  $("reviewBadge").textContent = reviewNames[shot.review?.state] || "未确认"; $("reviewBadge").className = `review-badge ${shot.review?.state || "unreviewed"}`;
  $("field-summary").value = claimText(shot.analysis?.summary?.value); $("origin-summary").innerHTML = originMarkup(shot.analysis?.summary);
  $("visualFields").innerHTML = visualFields.map(field => makeField(field, shot)).join("");
  $("storyFields").innerHTML = storyFields.map(field => makeField(field, shot, true)).join("");
  $("emotionFields").innerHTML = emotionFields.map(([field, label]) => `<label for="emotion-${field}">${label}<input id="emotion-${field}" data-emotion="${field}" type="number" min="0" max="1" step="0.01" value="${typeof shot.emotion?.[field]?.value === "number" ? shot.emotion[field].value : ""}" placeholder="未标记"><span class="origin-info">${originMarkup(shot.emotion?.[field])}</span></label>`).join("");
  $("reviewNote").value = shot.review?.note || "";
  const warnings = [...(shot.warnings || [])];
  if (shot.status === "failed") warnings.unshift("这个镜头的分析未完成，可以继续分析或人工补充。");
  $("shotWarnings").hidden = !warnings.length; $("shotWarnings").textContent = warnings.map(warning => typeof warning === "string" ? warning : JSON.stringify(warning)).join("\n");
  const boundary = shot.boundary || {};
  const claims = [...Object.values(shot.analysis || {}), ...Object.values(shot.emotion || {})].filter(value => value && typeof value === "object" && value.value != null);
  const evidenceIds = [...new Set(claims.flatMap(claim => claim.evidence_ids || []))];
  const runs = (state.document.runs || []).filter(run => claims.some(claim => claim.run_id === run.id));
  $("evidenceInfo").innerHTML = `<div class="evidence-row"><b>边界方法</b><br>${escapeHtml(boundary.method || "未知")}${typeof boundary.confidence === "number" ? ` · ${Math.round(boundary.confidence * 100)}%` : ""}</div><div class="evidence-row"><b>关键帧证据</b><br>${evidenceIds.length || 0} 个引用 · ${shot.keyframes?.length || 0} 张可用帧</div>${(shot.keyframes || []).map(frame => `<div class="evidence-row"><button type="button" class="text-button" data-frame="${escapeHtml(frame.id)}">${frame.time_us == null ? "查看图片" : `跳转证据帧 ${formatTime(frame.time_us)}`}</button></div>`).join("")}${runs.map(run => `<div class="evidence-row"><b>${escapeHtml(run.provider || run.kind)}</b><br>${escapeHtml(run.model || "本地测量")}<br>${escapeHtml(run.completed_at || run.started_at || "")}</div>`).join("")}<div class="evidence-row">AI 置信值为模型自报，未经校准。关键帧并不等于穷尽镜头全部内容。</div>`;
  renderRegionList(); updateShotSelection(false); setDirty(false); $("annotateButton").disabled = !currentFrame(); $("addRegion").disabled = !currentFrame();
}
function setDirty(dirty = true) { state.dirty = dirty; $("editStatus").textContent = dirty ? "有未保存的修改" : "所有改动保存到记录文件"; $("editStatus").classList.toggle("dirty", dirty); }
function parseField(field, text, original) {
  const value = text.trim(); if (!value) return null;
  if (field === "tags" || Array.isArray(original) && original.every(item => typeof item === "string")) return value.split(/[,，、\n]+/).map(item => item.trim()).filter(Boolean);
  if (original && typeof original === "object") { try { return JSON.parse(value); } catch { throw new Error("结构化字段需要保留有效 JSON 格式。请核对主体、动作或场景等字段。"); } }
  return value;
}
function gatherEdits() {
  const shot = currentShot(), analysis = {}, emotion = {}; if (!shot) return {};
  ["summary", ...visualFields.map(field => field[0]), ...storyFields.map(field => field[0])].forEach(field => {
    const original = shot.analysis?.[field]?.value ?? null, text = $(`field-${field}`).value;
    if (text !== claimText(original)) analysis[field] = parseField(field, text, original);
  });
  emotionFields.forEach(([field]) => {
    const input = $(`emotion-${field}`), text = input.value.trim(), value = text === "" ? null : Number(text);
    if (value !== null && (!Number.isFinite(value) || value < 0 || value > 1)) throw new Error("情绪数值必须在 0 到 1 之间，未知请留空。");
    if (value !== (shot.emotion?.[field]?.value ?? null)) emotion[field] = value;
  });
  const patch = {revision: state.document.revision};
  if (Object.keys(analysis).length) patch.analysis = analysis;
  if (Object.keys(emotion).length) patch.emotion = emotion;
  if ($("reviewNote").value !== (shot.review?.note || "")) patch.review = {state: "edited", note: $("reviewNote").value};
  return patch;
}
function setSaving(saving) { state.busy = saving; $("inspectorForm").inert = saving || !!state.loadingAsset; ["saveButton","acceptButton","rejectButton"].forEach(id => $(id).disabled = saving); $("saveButton").textContent = saving ? "保存中…" : "保存修改"; }
async function saveShot({reviewState, regions, success = "镜头记录已保存"} = {}) {
  if (state.busy || state.loadingAsset || !currentShot()) return false;
  const assetId = state.assetId, shotId = currentShot().id;
  try {
    const patch = gatherEdits();
    if (reviewState) patch.review = {state: reviewState, note: $("reviewNote").value};
    if (regions) patch.regions = regions;
    if (Object.keys(patch).length === 1) { showToast("当前记录没有待保存的修改"); setDirty(false); return true; }
    setSaving(true);
    const doc = await api(`/api/assets/${encodeURIComponent(assetId)}/shots/${encodeURIComponent(shotId)}`, {method:"PATCH", body:JSON.stringify(patch)});
    if (state.assetId === assetId) { state.document = doc; state.shotIndex = Math.max(0, doc.shots.findIndex(shot => shot.id === shotId)); renderInspector(); renderShotStrip(); renderRegions(); renderTimeline(); $("recordStatus").textContent = `帧脉记录 v${doc.spec_version} · 修订 ${doc.revision}`; }
    showToast(success); loadAssets().catch(() => {}); return true;
  } catch (error) { showToast(error.message, true); return false; } finally { setSaving(false); }
}

function fitMedia() {
  if (!state.document) return;
  const tech = state.document.asset.technical || {}, isVideo = state.document.asset.kind === "video";
  const width = (isVideo ? $("video").videoWidth : $("image").naturalWidth) || tech.width || 16;
  const height = (isVideo ? $("video").videoHeight : $("image").naturalHeight) || tech.height || 9;
  const rect = $("mediaStage").getBoundingClientRect(), scale = Math.min(rect.width / width, rect.height / height);
  $("mediaSurface").style.width = `${Math.max(0, width * scale)}px`; $("mediaSurface").style.height = `${Math.max(0, height * scale)}px`;
}
function renderRegions() {
  const shot = currentShot(); if (!shot) return;
  const frame = currentFrame();
  const isAtFrame = state.document.asset.kind === "image" || (Math.abs($("video").currentTime * 1e6 - (frame?.time_us ?? 0)) <= 200000 && $("video").paused);
  $("regionOverlay").innerHTML = isAtFrame || state.drawing ? (shot.regions || []).filter(region => region.keyframe_id === frame?.id).map(region => `<div class="region-box" style="left:${region.xywh[0] * 100}%;top:${region.xywh[1] * 100}%;width:${region.xywh[2] * 100}%;height:${region.xywh[3] * 100}%"><span>${escapeHtml(region.label)}</span></div>`).join("") : "";
}
function renderRegionList() {
  const regions = currentShot()?.regions || [];
  $("regionList").innerHTML = regions.length ? regions.map((region, index) => `<div class="region-item"><button type="button" data-region-jump="${index}">${escapeHtml(region.label || "未命名区域")}<small>${region.xywh.map(value => Number(value).toFixed(3)).join(" / ")}</small></button><button type="button" class="icon-button" data-region-delete="${index}" title="删除区域" aria-label="删除 ${escapeHtml(region.label)}">×</button></div>`).join("") : '<p class="field-hint">尚无画面区域标记</p>';
}
function startDrawing() {
  if (!currentFrame() || state.busy || state.loadingAsset) return;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  seekToShot(true); state.drawing = true; $("regionOverlay").classList.add("drawing"); $("annotationHint").hidden = false; $("annotateButton").classList.add("active"); renderRegions();
}
function stopDrawing() { state.drawing = false; regionPointer = null; $("regionOverlay").classList.remove("drawing"); $("annotationHint").hidden = true; $("annotateButton").classList.remove("active"); $("regionOverlay").querySelector(".draft")?.remove(); }
function normalizedPointer(event) { const rect = $("regionOverlay").getBoundingClientRect(); return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))]; }
$("regionOverlay").addEventListener("pointerdown", event => {
  if (!state.drawing || event.button !== 0) return; event.preventDefault();
  const point = normalizedPointer(event); regionPointer = {id:event.pointerId, start:point, end:point}; $("regionOverlay").setPointerCapture(event.pointerId);
  const box = document.createElement("div"); box.className = "region-box draft"; $("regionOverlay").append(box);
});
$("regionOverlay").addEventListener("pointermove", event => {
  if (!regionPointer || regionPointer.id !== event.pointerId) return; regionPointer.end = normalizedPointer(event);
  const [sx,sy] = regionPointer.start, [ex,ey] = regionPointer.end, box = $("regionOverlay").querySelector(".draft");
  if (box) Object.assign(box.style,{left:`${Math.min(sx,ex)*100}%`,top:`${Math.min(sy,ey)*100}%`,width:`${Math.abs(ex-sx)*100}%`,height:`${Math.abs(ey-sy)*100}%`});
});
$("regionOverlay").addEventListener("pointerup", event => {
  if (!regionPointer || regionPointer.id !== event.pointerId) return;
  const [sx,sy] = regionPointer.start, [ex,ey] = normalizedPointer(event); regionPointer = null;
  if (Math.abs(ex-sx) < .008 || Math.abs(ey-sy) < .008) { $("regionOverlay").querySelector(".draft")?.remove(); return; }
  state.draftRegion = {id:`region-${crypto.randomUUID()}`,keyframe_id:currentFrame().id,xywh:[Math.min(sx,ex),Math.min(sy,ey),Math.abs(ex-sx),Math.abs(ey-sy)].map(v => Number(v.toFixed(6))),label:"",origin:"human",confidence:null};
  $("regionLabel").value = ""; $("regionDialog").showModal(); $("regionLabel").focus();
});
$("regionOverlay").addEventListener("pointercancel", () => { regionPointer = null; $("regionOverlay").querySelector(".draft")?.remove(); });
$("regionForm").addEventListener("submit", async event => { event.preventDefault(); if (!state.draftRegion) return; const region = {...state.draftRegion,label:$("regionLabel").value.trim()}; if (!region.label) return; const okay = await saveShot({regions:[...(currentShot().regions || []),region],success:"画面区域已保存"}); if (okay) { $("regionDialog").close(); state.draftRegion = null; stopDrawing(); renderRegions(); } });
$("regionDialog").addEventListener("close", () => { state.draftRegion = null; stopDrawing(); renderRegions(); });
$("regionList").addEventListener("click", async event => {
  const jump = event.target.closest("[data-region-jump]"), remove = event.target.closest("[data-region-delete]");
  if (jump) { const region = currentShot().regions[Number(jump.dataset.regionJump)]; state.keyframeId = region.keyframe_id; seekToShot(true); renderRegions(); }
  if (remove && !state.busy) { const index = Number(remove.dataset.regionDelete); if (window.confirm(`删除区域「${currentShot().regions[index].label}」？`)) await saveShot({regions:currentShot().regions.filter((_,i) => i !== index),success:"画面区域已删除"}); }
});

function renderTimeline() {
  const shots = state.document?.shots || []; if (!shots.length) return;
  const isVideo = state.document.asset.kind === "video", total = state.document.asset.technical?.duration_us || 1;
  const left = 32, right = 985, plotWidth = right-left, top = 13, bottom = 62;
  const xAt = shot => isVideo && shot.range ? left + ((shot.range.start_us + shot.range.end_us) / 2 / total) * plotWidth : (left + right) / 2;
  const yAt = value => bottom - value * (bottom-top);
  let svg = `<svg viewBox="0 0 1000 94" preserveAspectRatio="none" role="img" aria-label="镜头时长与情绪曲线，空白表示数据缺失"><line x1="${left}" x2="${right}" y1="${top}" y2="${top}" stroke="#263441" stroke-dasharray="3 5"/><line x1="${left}" x2="${right}" y1="${(top+bottom)/2}" y2="${(top+bottom)/2}" stroke="#24313e" stroke-dasharray="3 5"/><line x1="${left}" x2="${right}" y1="${bottom}" y2="${bottom}" stroke="#263441"/><text x="8" y="${top+3}">1.0</text><text x="8" y="${bottom+3}">0.0</text>`;
  let known = 0;
  const colors = {tension:"#f3bd54",valence:"#70c4a7",arousal:"#82b5e2"};
  emotionFields.forEach(([field]) => {
    let path = "", continued = false, circles = "";
    shots.forEach(shot => {
      const value = shot.emotion?.[field]?.value;
      if (typeof value !== "number" || !Number.isFinite(value)) { continued = false; return; }
      known++; const x = xAt(shot), y = yAt(value); path += `${continued ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)} `; continued = true;
      circles += `<circle cx="${x}" cy="${y}" r="2.3" fill="${colors[field]}"><title>${escapeHtml(field)} ${value}</title></circle>`;
    });
    svg += `<path class="${field}-line" d="${path}"/>${circles}`;
  });
  shots.forEach((shot,index) => {
    const x = isVideo && shot.range ? left + (shot.range.start_us / total) * plotWidth : left;
    const width = isVideo && shot.range ? Math.max(.5,(shot.range.end_us-shot.range.start_us) / total * plotWidth - 1) : plotWidth;
    svg += `<rect class="shot-segment${index === state.shotIndex ? " current" : ""}" data-timeline-shot="${index}" x="${x}" y="72" width="${width}" height="6" rx="1"><title>镜头 ${index+1}${shot.range ? ` · ${durationLabel(shot.range.end_us-shot.range.start_us)}` : ""}</title></rect>`;
  });
  svg += `<text x="${left}" y="90">${isVideo ? "00:00" : "IMAGE"}</text><text x="${right}" y="90" text-anchor="end">${isVideo ? formatTime(total) : "单张画面"}</text><line id="playhead" class="playhead" x1="${left}" x2="${left}" y1="7" y2="80"/></svg>`;
  $("timeline").innerHTML = svg;
  $("timelineHint").textContent = known ? "解读值 · 空白处无数据" : "尚无情绪标记 · 仅显示真实镜头时长";
  if (!isVideo) $("timelineHint").textContent = known ? "单张画面的情绪解读" : "静态图片 · 尚无情绪标记";
  updatePlayhead();
}
function updatePlayhead() {
  const playhead = $("playhead"); if (!playhead || !state.document) return;
  const total = state.document.asset.technical?.duration_us || 1, time = (state.pendingSeek?.time ?? $("video").currentTime) * 1e6;
  const x = state.document.asset.kind === "image" ? 508.5 : 32 + Math.min(1,Math.max(0,time/total)) * 953;
  playhead.setAttribute("x1",x); playhead.setAttribute("x2",x);
}
function onTimeUpdate() {
  if (!currentMediaMatches() || state.pendingSeek) return;
  const video = $("video"); $("currentTime").textContent = formatTime(video.currentTime * 1e6); $("seek").value = video.currentTime || 0;
  updatePlayhead();
  if (state.document && !video.paused && !state.dirty && !state.drawing && !state.loadingAsset) {
    const time = video.currentTime * 1e6, index = state.document.shots.findIndex(shot => shot.range && time >= shot.range.start_us && time < shot.range.end_us);
    if (index >= 0 && index !== state.shotIndex) selectShot(index,{seek:false,scroll:true,ask:false});
  }
  if (performance.now() - lastTimelineUpdate > 120) { renderRegions(); lastTimelineUpdate = performance.now(); }
}
async function togglePlayback() {
  if (!state.document || state.document.asset.kind !== "video") return;
  stopDrawing();
  if ($("video").paused) { try { await $("video").play(); } catch(error) { showToast(`视频无法播放：${error.message}`,true); } } else $("video").pause();
}
$("video").addEventListener("timeupdate",onTimeUpdate);
$("video").addEventListener("play",() => { $("playButton").textContent = "Ⅱ"; $("playButton").setAttribute("aria-label","暂停"); renderRegions(); });
$("video").addEventListener("pause",() => { $("playButton").textContent = "▶"; $("playButton").setAttribute("aria-label","播放"); renderRegions(); });
$("video").addEventListener("loadedmetadata",() => { if (!currentMediaMatches()) return; fitMedia(); if (Number.isFinite($("video").duration)) $("seek").max = $("video").duration; applyPendingSeek(); });
$("video").addEventListener("loadeddata",applyPendingSeek);
$("video").addEventListener("canplay",applyPendingSeek);
$("image").addEventListener("load",fitMedia);
$("video").addEventListener("error",() => { if ($("video").getAttribute("src") && state.document?.asset.kind === "video") { $("mediaError").textContent = "浏览器无法播放此视频。源文件可能已移动，或编码不受当前浏览器支持。镜头记录和关键帧仍可查看。"; $("mediaError").hidden = false; } });
$("image").addEventListener("error",() => { if (state.document?.asset.kind === "image") { $("mediaError").textContent = "图片无法读取。请检查源文件是否仍在原位置。"; $("mediaError").hidden = false; } });
new ResizeObserver(fitMedia).observe($("mediaStage"));
$("seek").addEventListener("input",() => { stopDrawing(); requestSeek(Number($("seek").value)); onTimeUpdate(); });
$("playButton").addEventListener("click",togglePlayback);
$("video").addEventListener("click",togglePlayback);
$("previousShot").addEventListener("click",() => selectShot(state.shotIndex-1));
$("nextShot").addEventListener("click",() => selectShot(state.shotIndex+1));
$("fullscreenButton").addEventListener("click",async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await $("mediaStage").requestFullscreen(); } catch { showToast("当前浏览器不支持全屏预览",true); } });
$("shotStrip").addEventListener("click",event => { const button = event.target.closest("[data-shot]"); if (button) selectShot(Number(button.dataset.shot),{scroll:false}); });
$("timeline").addEventListener("click",event => { const rect = event.target.closest("[data-timeline-shot]"); if (rect) selectShot(Number(rect.dataset.timelineShot)); });
$("assetList").addEventListener("click",event => { const button = event.target.closest("[data-asset]"); if (button) loadAsset(button.dataset.asset); });
$("assetSearch").addEventListener("input",renderLibrary);
$("refreshLibrary").addEventListener("click",async () => { try { if (state.dirty && !mayDiscard()) return; if (state.dirty) setDirty(false); await loadAssets({autoSelect:true,refreshDocument:true}); showToast("素材库已刷新"); } catch(error) { showToast(error.message,true); } });
$("inspectorForm").addEventListener("input",() => setDirty());
$("inspectorForm").addEventListener("submit",event => { event.preventDefault(); saveShot(); });
$("acceptButton").addEventListener("click",() => saveShot({reviewState:"accepted",success:"镜头已确认，判断来源已保留"}));
$("rejectButton").addEventListener("click",() => saveShot({reviewState:"rejected",success:"镜头已标记为待重做"}));
$("evidenceInfo").addEventListener("click",event => { const button = event.target.closest("[data-frame]"); if (button) { state.keyframeId = button.dataset.frame; seekToShot(true); renderRegions(); } });
$("annotateButton").addEventListener("click",() => state.drawing ? stopDrawing() : startDrawing());
$("addRegion").addEventListener("click",startDrawing);
$("annotationCancel").addEventListener("click",stopDrawing);

function renderJobs() {
  const jobs = state.jobs, active = jobs.filter(job => ["queued","running"].includes(job.state));
  $("jobCount").textContent = active.length ? `${active.length} 进行中` : jobs.length;
  if (!jobs.length) { $("jobList").innerHTML = '<p class="queue-empty">等待新的影像</p>'; return; }
  $("jobList").innerHTML = [...jobs].reverse().sort((a,b) => Number(["queued","running"].includes(b.state)) - Number(["queued","running"].includes(a.state))).slice(0,6).map(job => {
    const progress = job.progress || {}, total = Number(progress.total) || 0, current = Number(progress.current) || 0;
    const percent = job.state === "completed" ? 100 : total ? Math.min(100,Math.max(0,current/total*100)) : job.state === "running" ? 3 : 0;
    const path = state.submittedJobs.get(job.id)?.path || job.path || job.source || "素材分析";
    const description = job.error || progress.message || jobNames[job.state] || job.state;
    const canRetry = state.submittedJobs.has(job.id) || job.path;
    return `<div class="job ${escapeHtml(job.state)}"><div class="job-heading"><strong title="${escapeHtml(path)}">${escapeHtml(shortName(path))}</strong>${["queued","running"].includes(job.state) ? `<button class="text-button" data-job-cancel="${escapeHtml(job.id)}">取消</button>` : ["failed","cancelled"].includes(job.state) && canRetry ? `<button class="text-button" data-job-retry="${escapeHtml(job.id)}">重试</button>` : `<small>${escapeHtml(jobNames[job.state] || job.state)}</small>`}</div><div class="job-progress"><span style="width:${percent}%"></span></div><small>${escapeHtml(description)}${total && job.state === "running" ? ` (${current}/${total})` : ""}</small>${job.asset_id ? `<button class="text-button" data-job-open="${escapeHtml(job.asset_id)}">查看记录 ↗</button>` : ""}</div>`;
  }).join("");
}
async function pollJobs() {
  clearTimeout(pollTimer);
  try {
    const navigation = state.loadSequence;
    const data = await api("/api/jobs"); const jobs = data.jobs || []; let changedAsset = null, shouldReload = false;
    for (const job of jobs) {
      const previous = state.jobStates.get(job.id);
      if (previous && previous !== job.state && ["completed","failed","cancelled"].includes(job.state)) {
        if (job.state === "completed") { showToast("素材分析已完成，可以开始检查镜头记录"); changedAsset = job.asset_id; shouldReload = true; }
        if (job.state === "failed") { showToast(`分析未完成：${job.error || "请查看分析队列"}`,true); shouldReload = true; }
        if (job.state === "cancelled") { showToast("任务已取消，已完成的镜头记录会保留"); shouldReload = true; }
      }
      state.jobStates.set(job.id,job.state);
    }
    state.jobs = jobs; renderJobs();
    if (shouldReload) { await loadAssets(); if (changedAsset && !state.dirty && !state.busy && !state.loadingAsset && navigation === state.loadSequence) await loadAsset(changedAsset,{keepPosition:changedAsset === state.assetId,force:true}); }
  } catch(error) { $("jobCount").textContent = "连接中断"; }
  finally { pollTimer = setTimeout(pollJobs,document.hidden ? 6500 : 2000); }
}
$("jobList").addEventListener("click",async event => {
  const cancel = event.target.closest("[data-job-cancel]"), retry = event.target.closest("[data-job-retry]"), open = event.target.closest("[data-job-open]");
  try {
    if (cancel) { cancel.disabled = true; await api(`/api/jobs/${encodeURIComponent(cancel.dataset.jobCancel)}/cancel`,{method:"POST",body:"{}"}); await pollJobs(); }
    if (retry) { const old = state.jobs.find(job => job.id === retry.dataset.jobRetry), request = state.submittedJobs.get(retry.dataset.jobRetry); if (!request) { openImport(old.path); return; } retry.disabled = true; const result = await api("/api/analyze",{method:"POST",body:JSON.stringify(request)}); state.submittedJobs.set(result.id,request); state.jobStates.set(result.id,"queued"); showToast("任务已重新加入队列"); await pollJobs(); }
    if (open) await loadAsset(open.dataset.jobOpen);
  } catch(error) { showToast(error.message,true); if (cancel) cancel.disabled = false; if (retry) retry.disabled = false; }
});
function openImport(path = "") {
  $("sourcePath").value = path; $("importError").hidden = true;
  $("providerUrl").value = state.config.provider_url || "http://127.0.0.1:11434/v1"; $("providerModel").value = state.config.model || "";
  $("visionEnabled").checked = !!state.config.vision_available; $("providerOptions").hidden = !$("visionEnabled").checked;
  $("importDialog").showModal(); $("sourcePath").focus();
}
$("importOpen").addEventListener("click",() => openImport()); $("emptyImport").addEventListener("click",() => openImport());
$("reanalyzeButton").addEventListener("click",() => {
  if (!state.document) return; let path = state.document.asset.source?.uri || "";
  try { if (path.startsWith("file:")) { const url = new URL(path); path = decodeURIComponent(url.pathname); if (/^\/[A-Za-z]:/.test(path)) path = path.slice(1); if (url.hostname) path = `//${url.hostname}${path}`; } } catch {}
  openImport(path);
});
$("visionEnabled").addEventListener("change",() => $("providerOptions").hidden = !$("visionEnabled").checked);
$("browseButton").addEventListener("click",async () => {
  $("browseButton").disabled = true; $("browseButton").textContent = "选择中…";
  try { const data = await api("/api/pick",{method:"POST",body:"{}"}); if (data.path) $("sourcePath").value = data.path; }
  catch(error) { $("importError").textContent = `文件选择器无法打开：${error.message} 也可以直接粘贴完整路径。`; $("importError").hidden = false; }
  finally { $("browseButton").disabled = false; $("browseButton").textContent = "浏览…"; }
});
$("importForm").addEventListener("submit",async event => {
  event.preventDefault(); $("importError").hidden = true;
  const path = $("sourcePath").value.trim().replace(/^(["'])(.*)\1$/,"$2"); if (!path) return;
  const vision = $("visionEnabled").checked, request = {path,vision,threshold:Number($("threshold").value) || 27};
  if (vision) { request.provider_url = $("providerUrl").value.trim(); request.model = $("providerModel").value.trim(); if (!request.provider_url || !request.model) { $("importError").textContent = "请填写视觉模型服务地址与模型名称。"; $("importError").hidden = false; return; } }
  $("startAnalysis").disabled = true; $("startAnalysis").textContent = "创建任务…";
  try { const result = await api("/api/analyze",{method:"POST",body:JSON.stringify(request)}); state.submittedJobs.set(result.id,request); state.jobStates.set(result.id,"queued"); $("importDialog").close(); showToast(vision ? "开始拆镜与 AI 内容分析" : "开始本地拆镜与关键帧提取"); await pollJobs(); }
  catch(error) { $("importError").textContent = error.message; $("importError").hidden = false; }
  finally { $("startAnalysis").disabled = false; $("startAnalysis").innerHTML = '开始分析 <span>↗</span>'; }
});
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click",() => $(button.dataset.close).close()));
document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("click",event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
$("protocolOpen").addEventListener("click",async () => { $("protocolDialog").showModal(); try { const result = await api("/api/protocol"); $("protocolDetails").textContent = typeof result === "string" ? result : JSON.stringify(result,null,2); } catch(error) { $("protocolDetails").textContent = error.message; } });
$("workspaceNav").addEventListener("click",() => { document.querySelectorAll("dialog[open]").forEach(dialog => dialog.close()); $("main").scrollIntoView({block:"start"}); });
document.addEventListener("click",event => { if (!event.target.closest(".export-menu")) document.querySelectorAll(".export-menu[open]").forEach(menu => menu.open = false); });
document.addEventListener("keydown",event => {
  if (document.querySelector("dialog[open]") || event.target.closest("input,textarea,select,[contenteditable=true]") || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.code === "Space" && state.document?.asset.kind === "video" && !event.target.closest("button,a,summary")) { event.preventDefault(); togglePlayback(); }
  if (event.key === "ArrowLeft") { event.preventDefault(); selectShot(state.shotIndex-1); }
  if (event.key === "ArrowRight") { event.preventDefault(); selectShot(state.shotIndex+1); }
  if (event.key === "Escape") stopDrawing();
});
window.addEventListener("beforeunload",event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
async function boot() {
  const url = new URL(window.location.href), assetId = url.searchParams.get("asset"), shotId = url.searchParams.get("shot");
  try { state.config = await api("/api/config"); state.token = state.config.token; await loadAssets({autoSelect:!assetId}); if (assetId) await loadAsset(assetId,{shotId}); }
  catch(error) { $("assetList").innerHTML = '<p class="library-empty">无法读取素材库<br>请确认本地服务已启动后刷新</p>'; showToast(error.message,true); }
  pollJobs();
}
window.addEventListener("popstate",() => { const url = new URL(window.location.href), assetId = url.searchParams.get("asset"); if (assetId) loadAsset(assetId,{shotId:url.searchParams.get("shot")}); });
boot();
