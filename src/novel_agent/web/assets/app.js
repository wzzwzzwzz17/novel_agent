let currentId = null;
let currentSession = null;
let editingCharId = null;

async function api(path, opts = {}) {
  const timeoutMs = opts.timeoutMs || 20000;
  const { timeoutMs: _drop, ...rest } = opts;
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(rest.headers || {}) },
    signal: AbortSignal.timeout(timeoutMs),
    ...rest,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

function $(id) { return document.getElementById(id); }

let toastTimer = null;
function toast(message, bad = false) {
  const el = $("toast");
  el.textContent = message;
  el.classList.toggle("bad", bad);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2200);
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("panel-" + btn.dataset.tab).classList.add("active");
  };
});

async function loadLlmConfig() {
  const cfg = await api("/api/llm-config");
  $("apiBase").value = cfg.base_url || "";
  $("apiModel").value = cfg.model || "";
  $("apiKey").value = "";
  $("apiKeyStatus").textContent = cfg.key_set
    ? `已配置 ${cfg.key_hint || ""}，留空保存则不改密钥`
    : "尚未配置 API Key，续写前请先保存";
}

async function saveApi() {
  const btn = $("btnSaveApi");
  btn.disabled = true;
  try {
    const cfg = await api("/api/llm-config", {
      method: "PUT",
      body: JSON.stringify({
        base_url: $("apiBase").value.trim(),
        model: $("apiModel").value.trim(),
        api_key: $("apiKey").value.trim(),
      }),
    });
    $("apiKey").value = "";
    $("apiKeyStatus").textContent = cfg.key_set
      ? `API Key 已保存 ${cfg.key_hint || ""}`
      : "还没有 API Key";
    toast(cfg.key_set ? "API Key 已保存" : "已保存地址和模型，密钥仍为空", !cfg.key_set);
  } finally {
    btn.disabled = false;
  }
}

async function refreshHealth() {
  const h = await api("/api/health");
  const cloud = h.profile === "cloud" || h.model_mode === "api_only";
  $("health").textContent = cloud ? "DeepSeek API" : `${h.profile} · ${h.model_mode}`;
  const badge = $("localSpeed");
  if (badge) {
    badge.classList.toggle("live", cloud);
    badge.classList.toggle("down", !cloud && !h.local_llm_enabled);
    badge.textContent = cloud ? "云端 API" : "本地模型";
  }
  return h;
}

async function loadTags() {
  const tags = await api("/api/novelref/tags");
  const sel = $("refTag");
  sel.innerHTML = '<option value="">不使用参考</option>';
  for (const t of tags) {
    const o = document.createElement("option");
    o.value = t;
    o.textContent = t;
    sel.appendChild(o);
  }
}

async function loadSessions() {
  const list = await api("/api/sessions");
  const ul = $("sessionList");
  ul.innerHTML = "";
  for (const s of list) {
    const li = document.createElement("li");
    li.textContent = s.session_name;
    if (s.session_id === currentId) li.classList.add("active");
    li.onclick = () => openSession(s.session_id);
    ul.appendChild(li);
  }
}

function renderChat(history) {
  const box = $("chat");
  box.innerHTML = "";
  for (const m of history || []) {
    if (m.role !== "user" && m.role !== "assistant") continue;
    const div = document.createElement("div");
    div.className = `msg ${m.role}`;
    div.textContent = m.content;
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

function renderChars() {
  const cards = (currentSession && currentSession.character_list) || [];
  $("charCount").textContent = `本会话角色卡：${cards.length}（可继续新建，不会覆盖其他卡）`;
  const ul = $("charList");
  ul.innerHTML = "";
  for (const c of cards) {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${c.name}</strong><span class="meta">${c.active ? "活跃" : "停用"} · ${(c.optimized || c.raw_input || "").slice(0, 40)}</span>`;
    if (c.char_id === editingCharId) li.classList.add("active");
    li.onclick = () => fillCharForm(c);
    const del = document.createElement("button");
    del.className = "del";
    del.type = "button";
    del.textContent = "删";
    del.onclick = async (e) => {
      e.stopPropagation();
      await api(`/api/sessions/${currentId}/characters/${c.char_id}`, { method: "DELETE" });
      await openSession(currentId);
    };
    li.appendChild(del);
    ul.appendChild(li);
  }
}

function fillCharForm(c) {
  editingCharId = c.char_id;
  $("charName").value = c.name || "";
  $("charRaw").value = c.raw_input || "";
  $("charOpt").value = c.optimized || "";
  $("charActive").checked = !!c.active;
  renderChars();
}

function renderMemory() {
  const mem = (currentSession && currentSession.memory) || {};
  $("memSummary").value = mem.global_summary || "";
  const ul = $("eventList");
  ul.innerHTML = "";
  for (const e of mem.event_memory || []) {
    const li = document.createElement("li");
    li.innerHTML = `<strong>[${e.weight}]</strong> ${e.content}`;
    const del = document.createElement("button");
    del.className = "del";
    del.type = "button";
    del.textContent = "删";
    del.onclick = async () => {
      await api(`/api/sessions/${currentId}/memory/events/${e.event_id}`, { method: "DELETE" });
      await openSession(currentId);
    };
    li.appendChild(del);
    ul.appendChild(li);
  }
}

function renderConfig() {
  const c = (currentSession && currentSession.session_config) || {};
  $("cfgTemp").value = c.temperature ?? 0.85;
  $("cfgTopP").value = c.top_p ?? 0.9;
  $("cfgMaxTok").value = !c.max_tokens || c.max_tokens === 1600 ? 6000 : c.max_tokens;
  $("cfgTurns").value = c.recent_turns ?? 12;
  $("cfgMinChars").value = c.min_reply_chars ?? 800;
  $("cfgRef").checked = c.enable_novelref !== false;
  $("cfgMem").checked = c.enable_auto_memory !== false;
  $("cfgOoc").checked = c.enable_ooc_hint !== false;
}

function renderLogs(extra) {
  const logs = (currentSession && currentSession.tool_logs) || [];
  $("refLog").textContent = JSON.stringify({ latest: extra || null, tool_logs: logs.slice(-10) }, null, 2);
}

async function openSession(id) {
  currentId = id;
  currentSession = await api(`/api/sessions/${id}`);
  $("outline").value = currentSession.novel_outline || "";
  renderChat(currentSession.chat_history);
  renderChars();
  renderMemory();
  renderConfig();
  renderLogs();
  await loadSessions();
  if (window.matchMedia("(max-width: 840px)").matches) setPanel(false);
}

async function createSession() {
  const name = prompt("小说名称", "新小说") || "新小说";
  const s = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ session_name: name }),
  });
  editingCharId = null;
  await openSession(s.session_id);
}

async function saveOutline() {
  const status = $("outlineStatus");
  if (!currentId) {
    status.textContent = "请先选择或新建会话";
    toast("请先选择会话", true);
    return;
  }
  const btn = $("btnSaveOutline");
  btn.disabled = true;
  status.textContent = "正在保存…";
  try {
    await api(`/api/sessions/${currentId}/outline`, {
      method: "PUT",
      body: JSON.stringify({ novel_outline: $("outline").value }),
    });
    status.textContent = "总纲已保存";
    toast("总纲已保存");
  } catch (e) {
    status.textContent = "保存失败";
    toast(String(e.message || e), true);
  } finally {
    btn.disabled = false;
  }
}

function newChar() {
  editingCharId = null;
  $("charName").value = "";
  $("charRaw").value = "";
  $("charOpt").value = "";
  $("charActive").checked = true;
  renderChars();
  toast("正在编辑一张新角色卡");
}

async function saveChar() {
  if (!currentId) return alert("请先选择会话");
  const name = $("charName").value.trim();
  if (!name) return alert("需要角色名");
  const btn = $("btnAddChar");
  btn.disabled = true;
  const character = {
    char_id: editingCharId || crypto.randomUUID(),
    name,
    raw_input: $("charRaw").value,
    optimized: $("charOpt").value,
    active: $("charActive").checked,
    appearance: "",
    personality: "",
    background: "",
    speech_style: "",
    taboo: "",
    goal: "",
    relation: "",
  };
  // preserve fields if editing
  if (editingCharId && currentSession) {
    const old = currentSession.character_list.find((c) => c.char_id === editingCharId);
    if (old) Object.assign(character, old, {
      name: character.name,
      raw_input: character.raw_input,
      optimized: character.optimized || old.optimized,
      active: character.active,
    });
  }
  try {
    await api(`/api/sessions/${currentId}/characters`, {
      method: "POST",
      body: JSON.stringify({ character }),
    });
    editingCharId = character.char_id;
    await openSession(currentId);
    const n = (currentSession.character_list || []).length;
    toast(`角色卡已保存，本会话共 ${n} 张`);
  } finally {
    btn.disabled = false;
  }
}

async function saveToLibrary() {
  if (!$("charName").value.trim()) return toast("先填写角色名", true);
  await saveChar();
  const card = (currentSession.character_list || []).find((c) => c.char_id === editingCharId);
  if (!card) return;
  await api("/api/library/characters", {
    method: "POST",
    body: JSON.stringify({ character: card }),
  });
  await loadLibrary();
  toast("已存入角色库，可在其他会话导入");
}

async function loadLibrary() {
  const cards = await api("/api/library/characters");
  const sel = $("libSelect");
  sel.innerHTML = '<option value="">（空）</option>';
  for (const c of cards) {
    const o = document.createElement("option");
    o.value = c.char_id;
    o.textContent = c.name;
    sel.appendChild(o);
  }
}

async function importFromLibrary() {
  if (!currentId) return toast("请先选择会话", true);
  const id = $("libSelect").value;
  if (!id) return toast("先在角色库里选一张卡", true);
  currentSession = await api(`/api/sessions/${currentId}/characters/import/${id}`, { method: "POST" });
  renderChars();
  toast(`已导入，本会话共 ${currentSession.character_list.length} 张角色卡`);
}

async function loadPlugins() {
  const plugins = await api("/api/plugins");
  const ul = $("pluginList");
  ul.innerHTML = "";
  for (const p of plugins) {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${p.display_name}</strong><span class="meta">${p.version} · ${p.origin} · ${p.description}</span>`;
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = !!p.enabled;
    toggle.style.width = "auto";
    toggle.onchange = async () => {
      await api(`/api/plugins/${p.id}/enabled`, {
        method: "POST",
        body: JSON.stringify({ enabled: toggle.checked }),
      });
      toast(toggle.checked ? `已启用 ${p.display_name}` : `已停用 ${p.display_name}`);
    };
    li.appendChild(toggle);
    ul.appendChild(li);
  }
}

async function optimizeChar() {
  if (!currentId) return;
  if (!editingCharId) await saveChar();
  if (!editingCharId) return;
  $("btnOptChar").disabled = true;
  try {
    const char = await api(`/api/sessions/${currentId}/characters/${editingCharId}/optimize`, {
      method: "POST",
      body: "{}",
    });
    fillCharForm(char);
    await openSession(currentId);
  } catch (e) {
    alert(String(e.message || e));
  } finally {
    $("btnOptChar").disabled = false;
  }
}

async function saveSummary() {
  if (!currentId) return;
  await api(`/api/sessions/${currentId}/memory/summary`, {
    method: "PUT",
    body: JSON.stringify({ global_summary: $("memSummary").value }),
  });
  await openSession(currentId);
}

async function addEvent() {
  if (!currentId) return;
  const content = $("eventContent").value.trim();
  if (!content) return;
  await api(`/api/sessions/${currentId}/memory/events`, {
    method: "POST",
    body: JSON.stringify({
      event: {
        content,
        weight: $("eventWeight").value,
        source: "manual",
      },
    }),
  });
  $("eventContent").value = "";
  await openSession(currentId);
}

async function saveCfg() {
  if (!currentId || !currentSession) return;
  const session_config = {
    ...currentSession.session_config,
    temperature: Number($("cfgTemp").value),
    top_p: Number($("cfgTopP").value),
    max_tokens: Number($("cfgMaxTok").value),
    recent_turns: Number($("cfgTurns").value),
    min_reply_chars: Number($("cfgMinChars").value || 0),
    enable_novelref: $("cfgRef").checked,
    enable_auto_memory: $("cfgMem").checked,
    enable_ooc_hint: $("cfgOoc").checked,
  };
  await api(`/api/sessions/${currentId}/config`, {
    method: "PUT",
    body: JSON.stringify({ session_config }),
  });
  await openSession(currentId);
}

async function probeRef() {
  const kw = $("refTag").value;
  if (!kw) return alert("先选择关键词");
  const scene = $("sceneKw").value.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean);
  try {
    const result = await api("/api/novelref/probe", {
      method: "POST",
      body: JSON.stringify({ keyword: kw, scene_keywords: scene }),
    });
    $("refLog").textContent = JSON.stringify(result, null, 2);
  } catch (e) {
    alert(String(e.message || e));
  }
}

function paintSpeed(sample) {
  const el = $("localSpeed");
  if (!el || !sample) return;
  el.classList.remove("live", "down");
  if (!sample.online) {
    el.classList.add("down");
    el.textContent = `本地模型离线 · ${sample.note || ""}`;
    return;
  }
  const speed = sample.tok_per_sec;
  const speedText = speed == null ? "空闲" : `${Number(speed).toFixed(1)} tok/s`;
  if (speed > 0) el.classList.add("live");
  const name = sample.model ? ` ${sample.model}` : "";
  el.textContent = `本地模型已启用${name} · ${speedText}`;
}

async function pollSpeed() {
  try {
    paintSpeed(await api("/api/local-speed", { timeoutMs: 4000 }));
  } catch {
    paintSpeed({ online: false });
  }
}

function appendLiveBubble() {
  const box = $("chat");
  const div = document.createElement("div");
  div.id = "liveBubble";
  div.className = "msg assistant";
  div.textContent = "";
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

let genAbort = null;
let genClock = 0;
let genChars = 0;

function noteDelta(text) {
  if (!genClock) genClock = performance.now();
  genChars += text.length;
  const tokens = genChars * 0.6;
  const seconds = Math.max((performance.now() - genClock) / 1000, 0.05);
  paintSpeed({
    online: true,
    tok_per_sec: tokens / seconds,
    model: "实时",
    note: "生成中",
  });
}

async function writeStream(instruction, kw, scene) {
  const bubble = appendLiveBubble();
  genAbort = new AbortController();
  genClock = 0;
  genChars = 0;
  $("btnPause").disabled = false;
  const res = await fetch(`/api/sessions/${currentId}/write-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      instruction,
      use_reference_keyword: kw || null,
      scene_keywords: scene,
      auto: true,
    }),
    signal: genAbort.signal,
  });
  if (!res.ok || !res.body) throw new Error(await res.text());
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === "status" && !bubble.dataset.started) bubble.textContent = event.text;
      if (event.type === "replace") {
        bubble.textContent = "";
        delete bubble.dataset.started;
        genClock = 0;
        genChars = 0;
      }
      if (event.type === "delta") {
        if (!bubble.dataset.started) {
          bubble.textContent = "";
          bubble.dataset.started = "1";
        }
        bubble.textContent += event.text;
        noteDelta(event.text);
        $("chat").scrollTop = $("chat").scrollHeight;
      }
      if (event.type === "speed") {
        paintSpeed({ online: true, tok_per_sec: event.tok_per_sec, model: "实时", note: "生成中" });
      }
      if (event.type === "error") throw new Error(event.message || "stream error");
      if (event.type === "done") donePayload = event;
    }
  }
  return donePayload;
}

async function write() {
  if (!currentId) return alert("请先新建或选择会话");
  const instruction = $("instruction").value.trim();
  if (!instruction) return;
  const btn = $("btnWrite");
  btn.disabled = true;
  $("oocBox").classList.add("hidden");
  try {
    const kw = $("refTag").value || null;
    const scene = $("sceneKw").value.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean);
    if ($("streamMode").checked) {
      const result = await writeStream(instruction, kw, scene);
      if (result && result.ooc_hints && result.ooc_hints.length) {
        $("oocBox").textContent = "OOC 提示：" + result.ooc_hints.join("；");
        $("oocBox").classList.remove("hidden");
      }
      $("instruction").value = "";
      await openSession(currentId);
      return;
    }
    const result = await api(`/api/sessions/${currentId}/write`, {
      timeoutMs: 180000,
      method: "POST",
      body: JSON.stringify({
        instruction,
        use_reference_keyword: kw || null,
        scene_keywords: scene,
        auto: true,
      }),
    });
    if (result.ooc_hints && result.ooc_hints.length) {
      $("oocBox").textContent = "OOC 提示：" + result.ooc_hints.join("；");
      $("oocBox").classList.remove("hidden");
    }
    $("instruction").value = "";
    await openSession(currentId);
    renderLogs({
      reference: result.reference,
      memory: result.memory,
      provider: result.provider,
      ooc_hints: result.ooc_hints,
    });
  } catch (e) {
    if (e && e.name === "AbortError") {
      const bubble = $("liveBubble");
      const partial = bubble && bubble.dataset.started ? bubble.textContent : "";
      try {
        await api(`/api/sessions/${currentId}/interrupt`, {
          method: "POST",
          body: JSON.stringify({ instruction, partial }),
        });
        await openSession(currentId);
      } catch (err) {
        toast(String(err.message || err), true);
      }
      toast("已暂停。可点「重新生成」把这一轮写完");
    } else alert(String(e.message || e));
  } finally {
    btn.disabled = false;
    $("btnPause").disabled = true;
    genAbort = null;
  }
}

function setPanel(open) {
  document.body.classList.toggle("panel-open", open);
  const btn = $("btnPanel");
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
}

function bindKeyboardInset() {
  const vv = window.visualViewport;
  if (!vv) return;
  const apply = () => {
    const inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.documentElement.style.setProperty("--kb", `${Math.round(inset)}px`);
  };
  vv.addEventListener("resize", apply);
  vv.addEventListener("scroll", apply);
  apply();
}

$("btnNew").onclick = createSession;
$("btnPanel").onclick = () => setPanel(!document.body.classList.contains("panel-open"));
$("btnClosePanel").onclick = () => setPanel(false);
$("btnSaveOutline").onclick = saveOutline;
$("btnNewChar").onclick = newChar;
$("btnSaveLib").onclick = () => saveToLibrary().catch((e) => toast(String(e.message || e), true));
$("btnImportLib").onclick = () => importFromLibrary().catch((e) => toast(String(e.message || e), true));
$("btnAddChar").onclick = () => saveChar().catch((e) => toast(String(e.message || e), true));
$("btnOptChar").onclick = optimizeChar;
$("btnSaveSummary").onclick = saveSummary;
$("btnAddEvent").onclick = addEvent;
$("btnSaveCfg").onclick = saveCfg;
$("btnSaveApi").onclick = () => saveApi().catch((e) => toast(String(e.message || e), true));
$("btnProbeRef").onclick = probeRef;
$("btnWrite").onclick = write;
$("btnPause").onclick = () => {
  if (genAbort) genAbort.abort();
};
$("btnRegen").onclick = regenerate;

async function regenerate() {
  if (!currentId) return toast("请先选择会话", true);
  const history = (currentSession && currentSession.chat_history) || [];
  const last = history[history.length - 1];
  const can = last && (last.role === "assistant" || last.role === "user");
  if (!can) return toast("还没有可重新生成的回复", true);
  const btn = $("btnRegen");
  btn.disabled = true;
  toast("正在重新生成…");
  try {
    const result = await api(`/api/sessions/${currentId}/regenerate`, {
      method: "POST",
      timeoutMs: 180000,
      body: "{}",
    });
    await openSession(currentId);
    const extra = result.retried_for_length ? "，并因字数不足自动重写了一次" : "";
    toast(`已重新生成，${result.chars || (result.text || "").length} 字${extra}`);
    renderLogs({
      reference: result.reference,
      memory: result.memory,
      provider: result.provider,
      chars: result.chars,
    });
  } catch (e) {
    toast(String(e.message || e), true);
  } finally {
    btn.disabled = false;
  }
}

(async function init() {
  bindKeyboardInset();
  if (localStorage.getItem("novel-stream") !== "0") $("streamMode").checked = true;
  $("streamMode").onchange = () => localStorage.setItem("novel-stream", $("streamMode").checked ? "1" : "0");
  const health = await refreshHealth();
  if (!(health.profile === "cloud" || health.model_mode === "api_only")) {
    pollSpeed();
    setInterval(pollSpeed, 5000);
  }
  await loadLlmConfig();
  await loadTags();
  await loadPlugins();
  await loadLibrary();
  await loadSessions();
  const list = await api("/api/sessions");
  if (list.length) await openSession(list[0].session_id);
})();
