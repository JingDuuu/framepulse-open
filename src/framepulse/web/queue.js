"use strict";

(() => {
  let queueBusy = false, importBusy = false, models = [], localProviderUrl = "http://127.0.0.1:11434/v1", dragDepth = 0;
  let view = "review";
  let connectionTimer, connecting = false;
  const phaseNames = {queued:"等待处理",pending:"等待处理",hash:"识别素材",hashing:"识别素材",probe:"读取媒体信息",probing:"读取媒体信息",measure:"读取图片",measurement:"测量与拆镜",detect:"逐帧检测",decode:"逐帧测量",decoding:"逐帧测量",detection:"检测镜头边界",resume:"恢复检查点",keyframes:"提取证据关键帧",frames:"提取证据关键帧",preview:"制作播放预览",vision:"AI 镜头分析",ocr:"文字识别",overview:"整片概括",complete:"完成",completed:"完成",finalize:"写入记录"};
  const profiles = () => state.config.profiles || {balanced:{name:"均衡",description:"完整镜头测量与适量证据帧"}};
  const profileName = key => profiles()[key]?.name || profiles()[key]?.label || key || "均衡";
  const defaultVision = () => typeof state.config.vision === "boolean" ? state.config.vision : !!state.config.vision_available && isLocal(state.config.provider_url);
  const isLocal = url => { try { const parsed = new URL(url); return ["127.0.0.1","localhost","[::1]"].includes(parsed.hostname) && parsed.port === "11434"; } catch { return false; } };
  function configChanged() {
    const selected = state.config.profile || "balanced", profile = profiles()[selected] || {};
    $("queueProfileName").textContent = profileName(selected); $("queueProfileDescription").textContent = profile.description || "完整读取素材并生成结构化记录。";
    $("queueModelName").textContent = defaultVision() ? state.config.model || "尚未选择视觉模型" : "仅本地测量 · AI 已关闭";
    for (const id of ["settingsProfile","importProfile"]) {
      const input = $(id), old = input.value;
      input.innerHTML = Object.entries(profiles()).map(([key,value]) => `<option value="${escapeHtml(key)}">${escapeHtml(value.name || value.label || key)}</option>`).join("");
      input.value = profiles()[old] ? old : selected;
    }
  }
  function showQueue() {
    view = "queue"; stopDrawing(); $("video").pause(); $("main").hidden = true; document.querySelector(".inspector").hidden = true; $("queuePanel").hidden = false;
    $("workspaceNav").classList.remove("nav-active"); $("queueNav").classList.add("nav-active"); render();
  }
  function showReview() {
    view = "review"; $("queuePanel").hidden = true; $("main").hidden = false; document.querySelector(".inspector").hidden = false;
    $("workspaceNav").classList.add("nav-active"); $("queueNav").classList.remove("nav-active"); requestAnimationFrame(fitMedia);
  }
  function render() {
    const jobs = state.jobs || [], pending = jobs.filter(job => ["pending","queued"].includes(job.state)), active = jobs.filter(job => job.state === "running"), completed = jobs.filter(job => job.state === "completed"), failed = jobs.filter(job => job.state === "failed");
    const mode = state.queue?.state || "idle";
    $("queueNavCount").textContent = pending.length + active.length;
    const storageError = state.queue?.error;
    $("queueStateDot").className = storageError ? "error" : mode;
    $("queueStateLabel").textContent = storageError ? "存储故障 · 队列暂停" : mode === "paused" ? "队列已暂停" : mode === "running" ? "正在处理队列" : pending.length ? "就绪，等待开始" : jobs.length ? "队列待命" : "等待添加素材";
    $("queueStorageError").hidden = !storageError;
    $("queueStorageError").textContent = storageError ? `${storageError} 空间或权限恢复后，点击「恢复队列」重试。` : "";
    $("queueCounts").textContent = `${pending.length} 待处理 · ${completed.length} 完成${failed.length ? ` · ${failed.length} 失败` : ""}`;
    $("queueStart").disabled = queueBusy || !pending.length || mode === "running" || mode === "paused";
    $("queuePause").textContent = mode === "paused" ? "恢复队列" : "暂停队列";
    $("queuePause").disabled = queueBusy || (mode !== "running" && mode !== "paused");
    $("queuePauseNotice").hidden = mode !== "paused";
    $("queuePauseNotice").textContent = active.length ? "队列已暂停。正在处理的素材会继续完成，后续素材暂不启动。" : "队列已暂停。待处理素材保留在列表，点击「恢复队列」继续。";
    $("queueDropEmpty").hidden = !!jobs.length; $("queueTable").hidden = !jobs.length;
    const sorted = [...jobs].sort((a,b) => (a.state === "running" ? 0 : ["pending","queued"].includes(a.state) ? 1 : 2) - (b.state === "running" ? 0 : ["pending","queued"].includes(b.state) ? 1 : 2));
    $("queueRows").innerHTML = sorted.map(job => {
      const options = job.options || job.config || {}, progress = job.progress || {}, total = Number(progress.total) || 0, current = Number(progress.current) || 0;
      const percent = job.state === "completed" ? 100 : total ? Math.max(0,Math.min(100,current/total*100)) : 0;
      const phase = phaseNames[progress.phase] || progress.phase || jobNames[job.state] || job.state;
      const profile = job.profile || options.profile || "balanced", model = job.model || options.model, vision = job.vision ?? options.vision;
      return `<tr class="${escapeHtml(job.state)}"><td class="queue-source"><strong title="${escapeHtml(job.path || job.name)}">${escapeHtml(job.name || shortName(job.path))}</strong><small title="${escapeHtml(job.path)}">${escapeHtml(job.path || "")}</small><span class="queue-preset">${escapeHtml(profileName(profile))} · ${vision === false ? "仅测量" : escapeHtml(model || "AI 内容分析")}</span></td><td class="queue-stage"><span>${escapeHtml(phase)}${job.state === "running" && total ? ` · ${escapeHtml(formatProgress(progress))}` : ""}</span><div class="queue-bar ${escapeHtml(job.state)}"><span style="width:${percent}%"></span></div><small title="${escapeHtml(job.error || progress.message || "")}">${escapeHtml(job.error || progress.message || "")}</small></td><td><span class="queue-status ${escapeHtml(job.state)}">${escapeHtml(jobNames[job.state] || job.state)}</span></td><td><div class="queue-row-actions">${["pending","queued","running"].includes(job.state) ? `<button class="text-button" data-queue-cancel="${escapeHtml(job.id)}">取消</button>` : ["failed","cancelled"].includes(job.state) ? `<button class="text-button" data-queue-retry="${escapeHtml(job.id)}">重试</button>` : ""}${job.asset_id ? `<button class="text-button" data-queue-open="${escapeHtml(job.asset_id)}">查看 ↗</button>` : ""}</div></td></tr>`;
    }).join("");
    configChanged();
  }
  async function queueAction(action) {
    if (queueBusy) return; queueBusy = true; render();
    try { await api(`/api/queue/${action}`,{method:"POST",body:"{}"}); await pollJobs(); showToast(action === "pause" ? "已暂停后续任务，当前素材会继续完成" : "队列已启动"); }
    catch(error) { showToast(error.message,true); } finally { queueBusy = false; render(); }
  }
  const parsePaths = text => [...new Set(String(text).split(/\r?\n/).map(path => path.trim().replace(/^(["'])(.*)\1$/,"$2")).filter(Boolean))];
  function openImport(path = "") {
    configChanged(); $("sourcePath").value = Array.isArray(path) ? path.join("\n") : path; $("importProfile").value = state.config.profile || "balanced";
    $("importVision").checked = defaultVision(); $("importModelHint").textContent = state.config.model ? `${state.config.model}${isLocal(state.config.provider_url) ? " · 本机处理" : ` · 关键帧将发送到 ${state.config.provider_url}`}` : "尚未选择模型，可到「模型设置」配置"; $("importOcr").checked = false;
    $("importError").hidden = true; $("threshold").value = state.config.threshold || 27; $("importDialog").showModal(); $("sourcePath").focus();
  }
  async function pickPaths(mode) {
    const bridge = window.framepulseDesktop;
    if (mode === "folder" && typeof bridge?.pickFolder === "function") return await bridge.pickFolder();
    if (mode === "files" && typeof bridge?.pickFiles === "function") return await bridge.pickFiles();
    const result = await api("/api/pick",{method:"POST",body:JSON.stringify({mode})}); return result.paths || (result.path ? [result.path] : []);
  }
  async function pickIntoImport(mode, fresh = false) {
    try { const paths = await pickPaths(mode); if (!paths?.length) return; if (fresh) openImport(paths); else $("sourcePath").value = [...new Set([...parsePaths($("sourcePath").value),...paths])].join("\n"); }
    catch(error) { if ($("importDialog").open) { $("importError").textContent = error.message; $("importError").hidden = false; } else showToast(error.message,true); }
  }
  async function enqueue(paths, options = {}) {
    const unique = [...new Set(paths.filter(path => typeof path === "string" && path.trim()).map(path => path.trim()))]; if (!unique.length) return;
    const result = await api("/api/queue/add",{method:"POST",body:JSON.stringify({paths:unique,recursive:true,...options})});
    const jobs = result.jobs || []; jobs.forEach(job => state.jobStates.set(job.id,"pending"));
    const skipped = result.skipped || []; $("queueSkipped").hidden = !skipped.length;
    $("queueSkipped").textContent = skipped.length ? `有 ${skipped.length} 项未加入：\n${skipped.slice(0,30).map(item => typeof item === "string" ? item : `${shortName(item.path)} — ${item.reason || "已跳过"}`).join("\n")}${skipped.length > 30 ? "\n其余略。" : ""}` : "";
    showQueue(); await pollJobs(); showToast(jobs.length ? `已加入 ${jobs.length} 份素材${state.queue.state === "running" ? "，将按顺序处理" : "，点击开始队列"}` : "没有新增任务，请查看跳过原因"); return result;
  }
  $("queueNav").addEventListener("click",showQueue);
  $("queueStart").addEventListener("click",() => queueAction("start"));
  $("queuePause").addEventListener("click",() => queueAction(state.queue.state === "paused" ? "resume" : "pause"));
  $("queuePickFiles").addEventListener("click",() => pickIntoImport("files",true)); $("queuePickFolder").addEventListener("click",() => pickIntoImport("folder",true));
  $("queueEmptyAdd").addEventListener("click",() => openImport());
  $("browseButton").addEventListener("click",() => pickIntoImport("files")); $("browseFolderButton").addEventListener("click",() => pickIntoImport("folder"));
  $("importForm").addEventListener("submit",async event => {
    event.preventDefault(); if (importBusy) return; const paths = parsePaths($("sourcePath").value); if (!paths.length) return;
    const vision = $("importVision").checked;
    if (vision && !state.config.model) { $("importError").textContent = "请先在「模型设置」中选择一个可用的视觉模型，或关闭 AI 内容分析。"; $("importError").hidden = false; return; }
    importBusy = true; $("startAnalysis").disabled = true; $("startAnalysis").textContent = "扫描并加入…"; $("importError").hidden = true;
    try { await enqueue(paths,{profile:$("importProfile").value,vision,threshold:Number($("threshold").value) || 27,ocr:$("importOcr").checked}); $("importDialog").close(); }
    catch(error) { $("importError").textContent = error.message; $("importError").hidden = false; }
    finally { importBusy = false; $("startAnalysis").disabled = false; $("startAnalysis").textContent = "加入待处理列表"; }
  });
  $("queueRows").addEventListener("click",async event => {
    const cancel = event.target.closest("[data-queue-cancel]"), retry = event.target.closest("[data-queue-retry]"), open = event.target.closest("[data-queue-open]");
    const button = cancel || retry;
    try { if (button) { button.disabled = true; const id = cancel ? cancel.dataset.queueCancel : retry.dataset.queueRetry; await api(`/api/jobs/${encodeURIComponent(id)}/${cancel ? "cancel" : "retry"}`,{method:"POST",body:"{}"}); await pollJobs(); showToast(cancel ? "已请求取消，已完成记录会保留" : "已重新加入待处理队列"); } if (open) { showReview(); await loadAsset(open.dataset.queueOpen); } }
    catch(error) { showToast(error.message,true); if (button) button.disabled = false; }
  });

  function syncProviderFields() {
    const local = $("providerMode").value === "local"; $("localModelOptions").hidden = !local; $("compatibleOptions").hidden = local;
    $("providerOptions").hidden = false;
    ["providerUrl","providerModel","providerKey","clearProviderKey"].forEach(id => $(id).disabled = local);
  }
  async function connectOllama(automatic = false) {
    if (connecting || (automatic && !state.config.auto_connect)) return;
    connecting = true; $("connectOllama").disabled = true;
    try {
      const result = await api("/api/ollama/connect",{method:"POST",body:JSON.stringify({automatic})});
      state.config = {...state.config,...result.config}; configChanged();
      if (result.connected) {
        $("visionEnabled").checked = true; $("providerMode").value = "local";
        $("localModel").value = state.config.model;
        if ($("settingsDialog").open) { syncProviderFields(); await refreshModels(); }
        showToast(`已连接本地模型 ${state.config.model}，用于之后加入的任务`);
      } else if (!automatic) {
        await refreshModels();
        $("ollamaHelp").open = true;
      }
    } catch(error) { if (!automatic) { $("modelStatus").textContent = error.message; $("ollamaHelp").open = true; } }
    finally {
      connecting = false; $("connectOllama").disabled = false;
      clearTimeout(connectionTimer);
      if (state.config.auto_connect) connectionTimer = setTimeout(() => connectOllama(true), 20000);
    }
  }
  async function refreshModels() {
    $("refreshModels").disabled = true; $("modelStatus").textContent = "正在读取本地模型列表…";
    try {
      const result = await api(`/api/models?provider_url=${encodeURIComponent(localProviderUrl)}`); models = result.models || []; localProviderUrl = result.provider_url || localProviderUrl;
      const previous = $("localModel").value || state.config.model;
      const usable = model => model.vision === true && !model.remote;
      $("localModel").innerHTML = '<option value="">选择视觉模型</option>' + models.map(model => `<option value="${escapeHtml(model.name)}"${usable(model) ? "" : " disabled"}>${escapeHtml(model.name)}${model.remote ? "（云端模型，请手动配置）" : model.vision === null ? "（无法确认视觉能力）" : !model.vision ? "（非视觉模型）" : ""}</option>`).join("");
      $("localModel").value = models.some(model => model.name === previous && usable(model)) ? previous : result.suggested_model || models.find(usable)?.name || "";
      const count = models.filter(usable).length;
      $("modelStatus").textContent = result.errors?.length ? result.errors.map(item => typeof item === "string" ? item : JSON.stringify(item)).join("；") : count ? `已连接 Ollama · ${count} 个本地视觉模型。新下载的模型会出现在这里，不限定品牌或版本。` : "已连接 Ollama，但未发现本地视觉模型。请按下方步骤下载支持图片输入的模型。";
      $("ollamaHelp").open = !count;
    } catch(error) { $("localModel").innerHTML = '<option value="">无法读取模型列表</option>'; $("modelStatus").textContent = error.message; }
    finally { $("refreshModels").disabled = false; }
  }
  async function openSettings() {
    configChanged(); $("settingsError").hidden = true; $("providerKey").value = ""; $("clearProviderKey").checked = false;
    $("visionEnabled").checked = defaultVision(); $("providerMode").value = !state.config.provider_url || isLocal(state.config.provider_url) ? "local" : "compatible";
    $("providerUrl").value = state.config.provider_url || ""; $("providerModel").value = state.config.model || ""; $("settingsProfile").value = state.config.profile || "balanced";
    $("settingsProfileDescription").textContent = profiles()[$("settingsProfile").value]?.description || "";
    $("keyStatus").textContent = state.config.has_api_key ? "当前服务已配置 · 仅本次会话" : "仅当前会话";
    syncProviderFields(); $("settingsDialog").showModal(); await refreshModels();
  }
  $("settingsOpen").addEventListener("click",openSettings); $("queueSettingsOpen").addEventListener("click",openSettings);
  $("connectOllama").addEventListener("click",() => connectOllama(false));
  $("refreshModels").addEventListener("click",refreshModels); $("providerMode").addEventListener("change",() => { if ($("providerMode").value === "compatible" && isLocal(state.config.provider_url)) $("visionEnabled").checked = false; syncProviderFields(); }); $("visionEnabled").addEventListener("change",syncProviderFields);
  $("settingsProfile").addEventListener("change",() => $("settingsProfileDescription").textContent = profiles()[$("settingsProfile").value]?.description || "");
  $("providerUrl").addEventListener("input",() => { $("keyStatus").textContent = "密钥仅用于其绑定的服务地址"; if ($("providerUrl").value.trim() !== state.config.provider_url) { $("visionEnabled").checked = false; $("keyStatus").textContent = "新地址默认关闭 AI，请核对后手动开启"; } });
  $("settingsDialog").addEventListener("close",() => { $("providerKey").value = ""; $("clearProviderKey").checked = false; });
  $("settingsForm").addEventListener("submit",async event => {
    event.preventDefault(); const local = $("providerMode").value === "local", vision = $("visionEnabled").checked;
    const provider_url = local ? localProviderUrl : $("providerUrl").value.trim(), model = local ? $("localModel").value : $("providerModel").value.trim();
    if (vision && (!provider_url || !model)) { $("settingsError").textContent = "请选择视觉模型，并填写有效的服务地址。"; $("settingsError").hidden = false; return; }
    const payload = {vision,provider_url:provider_url || state.config.provider_url,model:model || state.config.model || "",profile:$("settingsProfile").value};
    if (!local && $("clearProviderKey").checked) payload.api_key = ""; else if (!local && $("providerKey").value) payload.api_key = $("providerKey").value;
    $("saveSettings").disabled = true; $("settingsError").hidden = true;
    try { const result = await api("/api/config",{method:"POST",body:JSON.stringify(payload)}); state.config = {...state.config,...(result.config || result)}; state.token = state.config.token || state.token; configChanged(); $("providerKey").value = ""; $("settingsDialog").close(); showToast("设置已保存，将用于之后加入的任务"); }
    catch(error) { $("settingsError").textContent = error.message; $("settingsError").hidden = false; }
    finally { delete payload.api_key; $("saveSettings").disabled = false; }
  });

  async function droppedPaths(paths) {
    $("dropOverlay").hidden = true; dragDepth = 0;
    if (!Array.isArray(paths) || !paths.length) return;
    if ($("importDialog").open) { $("sourcePath").value = [...new Set([...parsePaths($("sourcePath").value),...paths])].join("\n"); return; }
    try { await enqueue(paths); } catch(error) { showToast(error.message,true); }
  }
  function isFileDrag(event) { return Array.from(event.dataTransfer?.types || []).includes("Files"); }
  document.addEventListener("dragenter",event => { if (!isFileDrag(event)) return; event.preventDefault(); dragDepth++; $("dropOverlay").hidden = false; });
  document.addEventListener("dragover",event => { if (!isFileDrag(event)) return; event.preventDefault(); event.dataTransfer.dropEffect = "copy"; });
  document.addEventListener("dragleave",event => { if (!isFileDrag(event)) return; dragDepth = Math.max(0,dragDepth-1); if (!dragDepth) $("dropOverlay").hidden = true; });
  document.addEventListener("drop",async event => {
    if (!isFileDrag(event)) return; event.preventDefault(); dragDepth = 0; $("dropOverlay").hidden = true;
    try {
      const files = Array.from(event.dataTransfer.files || []), bridge = window.framepulseDesktop;
      if (typeof bridge?.pathsForFiles !== "function") { openImport(); $("importError").textContent = "当前浏览器不提供本地文件的完整路径。请用「选择文件 / 文件夹」，或在帧脉桌面应用中直接拖入。"; $("importError").hidden = false; return; }
      await droppedPaths(await bridge.pathsForFiles(files));
    } catch(error) { showToast(`无法读取拖入的路径：${error.message}`,true); }
  });
  window.addEventListener("framepulse-drop",event => droppedPaths(event.detail?.paths || []));
  window.framepulseDesktopDrop = droppedPaths;
  window.framepulseQueue = {render,configChanged,openImport,showReview,showQueue,connectOllama};
  configChanged();
})();
