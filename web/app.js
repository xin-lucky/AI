/* XIN · 紫色玻璃拟物 UI —— 主应用逻辑
 * 核心能力：SSE 流式对话 / 历史管理 / 记忆系统 / 人格 / 模型 / PDF / 反馈 / 主题
 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s = "") => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const fmtSize = (b) => b < 1024 ? `${b}B` : b < 1048576 ? `${(b / 1024).toFixed(1)}KB` : `${(b / 1048576).toFixed(1)}MB`;
const LZ = (n) => (n < 10 ? "0" : "") + n;

// 背景预设（内置 5 张）
const PRESET_BGS = [
  // 紫色渐变（默认）
  "linear-gradient(135deg, #2a1a5a 0%, #4b2e83 45%, #6b4ea0 100%)",
  "linear-gradient(135deg, #1a1a3e 0%, #3a3a7e 50%, #6e3c8f 100%)",
  "linear-gradient(135deg, #1e3a5f 0%, #2d5a87 50%, #4a7ba6 100%)",
  "linear-gradient(135deg, #2d3e2b 0%, #4d6b48 50%, #7a9d74 100%)",
  "linear-gradient(135deg, #3e2d1a 0%, #6b5030 50%, #a8865a 100%)",
];

const state = {
  page: "chat",
  conversations: [],
  currentCid: null,
  messages: [],
  attachments: [],
  streaming: false,
  abort: null,

  // 反馈
  feedbacks: [],
  feedbackFilter: "all",

  // 背景
  bgUrl: null,
  bgOpacity: 0.8,
  bgBlur: 8,
  bgMask: 0.4,
  theme: "purple",
  savedDraft: "",

  // PDF
  pdfFile: null,
  pdfFmt: "word-fidelity",
  pdfResult: null,
  pdfBound: false,

  // 记忆 / 人格 / 模型（旧数据）
  memories: [],
  models: [],
  persona: null,
  settings: null,
};

/* ==========================================================================
   启动
   ========================================================================== */
async function boot() {
  // 每日一句 / 冷知识 / 有趣话题
  const DAILY_QUOTES = [
    "「慢慢来，比较快。」—— 吉田直树",
    "大脑其实只占身体重量的 2%，却消耗了 20% 的能量。",
    "你知道吗？章鱼有三颗心脏、蓝色血液和九个大脑。",
    "据说，人一生中会遇见约 2920 万人，能打招呼的不到 3 万人。",
    "「如果一件事值得做，就值得做得烂。」—— G.K. 切斯特顿",
    "香蕉在植物学上是浆果，但草莓不是。",
    "每天喝够 2L 水的人，心情更容易变好（虽然没人知道为什么）。",
    "「你现在的气质里，藏着你读过的书、走过的路、爱过的人。」",
    "地球的一天在变快：6 亿年前，一天有 42 小时。",
    "试试对着镜子笑 10 秒，大脑会以为你真的开心。",
  ];
  const q = DAILY_QUOTES[Math.floor(Math.random() * DAILY_QUOTES.length)];
  const dqEl = $("#daily-quote"); if (dqEl) dqEl.textContent = q;

  bindNav();
  bindCollapse();
  bindChat();
  bindSidebar();
  bindPersona();
  bindModels();
  bindSettings();
  bindMemory();
  bindFeedback();
  bindModal();
  bindPdf();

  // 设置底部图标按钮
  $("#btn-settings-foot")?.addEventListener("click", () => switchPage("settings"));

  // 加载设置
  try {
    const data = await api("/api/settings");
    // 后端直接返回 settings 对象（无包装），PUT 才返回 {ok, settings}
    const set = data.settings || data;
    state.settings = set;
    state.bgOpacity = set.bg_opacity ?? 1;
    state.bgBlur = set.bg_blur ?? 8;
    state.bgMask = (set.bg_mask ?? 0.4);
    state.bgUrl = set.bg_url || "";
    applyTheme(set.theme || "purple");
    applyBgVars();
    state.dialectEnabled = set.dialect_enabled ?? true;
    // 自动记忆默认勾选（启动即同步，不依赖进入设置页）
    const memBox = $("#s-memory-enabled");
    const memVal = (set.memory_enabled !== false);
    if (memBox) memBox.checked = memVal;
    state._memLoaded = memVal;
  } catch (e) {
    applyTheme("purple");
  }

  // 加载对话列表
  await loadConversations();

  // 加载模型状态
  try { const md = await api("/api/models"); state.models = Array.isArray(md) ? md : (md.items || []); fillModelSelect(); } catch {}

  // 恢复草稿
  const draft = localStorage.getItem("xin_input_draft") || "";
  if (draft) $("#input").value = draft;
  $("#input").addEventListener("input", () => {
    localStorage.setItem("xin_input_draft", $("#input").value);
  });

  // 恢复上次打开的对话；没有记录就打开最近一个（保证历史聊天不“消失”）
  const lastCid = localStorage.getItem("xin_current_cid");
  if (lastCid && state.conversations.some((c) => c.id === lastCid)) {
    openConversation(lastCid);
  } else if (state.conversations.length) {
    openConversation(state.conversations[0].id);
  }

  // btn-more 下拉菜单
  $("#btn-more")?.addEventListener("click", (e) => {
    e.stopPropagation();
    $("#more-dropdown")?.classList.toggle("hidden");
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#more-dropdown") && !e.target.closest("#btn-more")) {
      $("#more-dropdown")?.classList.add("hidden");
    }
  });
}

/* ==========================================================================
   API 封装
   ========================================================================== */
async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  if (r.status === 204) return null;
  return r.json();
}

/* ==========================================================================
   导航 & 页面切换
   ========================================================================== */
function bindNav() {
  $$(".menu-item").forEach((btn) => {
    btn.addEventListener("click", () => switchPage(btn.dataset.page));
  });
}

function bindCollapse() {
  const app = document.querySelector(".app");
  const state = { a: false };

  function apply() {
    app.style.setProperty("--side-a", state.a ? "0px" : "300px");
    app.classList.toggle("col-a", state.a);
  }

  // 恢复上次状态
  if (localStorage.getItem("xin_col_a") === "1") state.a = true;
  apply();

  $("#toggle-a")?.addEventListener("click", () => {
    state.a = !state.a;
    localStorage.setItem("xin_col_a", state.a ? "1" : "0");
    apply();
  });
}

function switchPage(page) {
  state.page = page;
  $$(".menu-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((s) => s.classList.toggle("active", s.id === `page-${page}`));
  document.body.dataset.currentPage = page;
  // 懒加载
  if (page === "settings") loadSettingsPage();
}

/* ==========================================================================
   对话列表侧栏
   ========================================================================== */
async function loadConversations() {
  try {
    const r = await api("/api/conversations");
    state.conversations = Array.isArray(r) ? r : (r.items || []);
  } catch {
    state.conversations = [];
  }
  // 置顶永远排在最前
  const pins = JSON.parse(localStorage.getItem("xin_pinned_cids") || "[]");
  state.conversations.sort((a, b) => {
    const pa = pins.includes(a.id) ? 1 : 0;
    const pb = pins.includes(b.id) ? 1 : 0;
    if (pa !== pb) return pb - pa;  // pinned first
    return (b.updated_at || "").localeCompare(a.updated_at || "");
  });
  renderChatList();
}

let selectMode = false;
const selectedCids = new Set();

function visibleConversations() {
  const search = ($("#chat-search")?.value || "").toLowerCase();
  return state.conversations.filter((c) => !search || (c.title || "").toLowerCase().includes(search));
}

function setSelectMode(on) {
  selectMode = on;
  selectedCids.clear();
  $("#btn-select")?.classList.toggle("active", on);
  $("#conv-batch")?.classList.toggle("hidden", !on);
  renderChatList();
}

function renderChatList() {
  const box = $("#chat-list");
  box.innerHTML = "";
  const pins = JSON.parse(localStorage.getItem("xin_pinned_cids") || "[]");
  const list = visibleConversations();
  list.forEach((c) => {
      const checked = selectedCids.has(c.id);
      const item = document.createElement("div");
      item.className = `conv-item${c.id === state.currentCid ? " active" : ""}${pins.includes(c.id) ? " pinned" : ""}${checked ? " selected" : ""}`;
      item.innerHTML = `
        ${selectMode ? `<span class="conv-check${checked ? " on" : ""}">${checked ? "✓" : ""}</span>` : ""}
        <div class="chat-item-text">
          <div class="chat-item-title">${esc(c.title || "未命名")}</div>
          <div class="chat-item-meta">${fmtDate(c.updated_at)}</div>
        </div>
        ${selectMode ? "" : `
        <button class="conv-3dot" title="更多">⋯</button>
        <div class="conv-menu hidden">
          <div class="conv-menu-item pin">${pins.includes(c.id) ? "📍 取消置顶" : "📌 置顶"}</div>
          <div class="conv-menu-item rename">✏️ 重命名</div>
          <div class="conv-menu-item del">🗑️ 删除</div>
        </div>`}
      `;
      item.addEventListener("click", (e) => {
        if (e.target.closest(".conv-menu")) return;
        if (e.target.classList.contains("conv-3dot")) return;
        if (selectMode) {
          if (selectedCids.has(c.id)) selectedCids.delete(c.id); else selectedCids.add(c.id);
          renderChatList();
          return;
        }
        openConversation(c.id);
      });
      if (selectMode) { box.appendChild(item); return; }
      const dotBtn = item.querySelector(".conv-3dot");
      const menu = item.querySelector(".conv-menu");
      dotBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        // 先关掉所有其他菜单
        $$(".conv-menu").forEach((m) => { if (m !== menu) m.classList.add("hidden"); });
        menu.classList.toggle("hidden");
      });
      menu.querySelector(".del").addEventListener("click", async (e) => {
        e.stopPropagation(); menu.classList.add("hidden");
        if (!confirm(`确定删除「${c.title || "未命名"}」？`)) return;
        try { await api(`/api/conversations/${c.id}`, { method: "DELETE" }); } catch {}
        if (c.id === state.currentCid) { state.currentCid = null; state.messages = []; localStorage.removeItem("xin_current_cid"); renderChat(); }
        await loadConversations();
      });
      menu.querySelector(".pin").addEventListener("click", (e) => {
        e.stopPropagation(); menu.classList.add("hidden");
        const pins = JSON.parse(localStorage.getItem("xin_pinned_cids") || "[]");
        const idx = pins.indexOf(c.id);
        if (idx >= 0) pins.splice(idx, 1); else pins.unshift(c.id);
        localStorage.setItem("xin_pinned_cids", JSON.stringify(pins));
        const isNowPinned = pins.includes(c.id);
        menu.querySelector(".pin").innerHTML = isNowPinned ? "📍 取消置顶" : "📌 置顶";
        // 重排对话列表：置顶按 pin 顺序在前，其余按更新时间
        state.conversations.sort((a, b) => {
          const pa = pins.indexOf(a.id); const pb = pins.indexOf(b.id);
          if (pa >= 0 && pb < 0) return -1;
          if (pb >= 0 && pa < 0) return 1;
          if (pa >= 0 && pb >= 0) return pa - pb;
          return (b.updated_at || "").localeCompare(a.updated_at || "");
        });
        renderChatList(); toast(idx >= 0 ? "已取消置顶" : "已置顶");
      });
      menu.querySelector(".rename").addEventListener("click", async (e) => {
        e.stopPropagation(); menu.classList.add("hidden");
        const name = prompt("重命名对话：", c.title || "未命名");
        if (!name) return;
        try {
          await api(`/api/conversations/${c.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: name }),
          });
          c.title = name;
          if (c.id === state.currentCid) $("#chat-title").textContent = name;
          renderChatList(); toast("已重命名 ✓");
        } catch (err) { toast("重命名失败：" + err.message, true); }
      });
      box.appendChild(item);
    });
  const titleEl = $("#conv-head-title");
  if (titleEl) titleEl.textContent = selectMode ? `已选 ${selectedCids.size} 项` : "对话记录";
  const allBtn = $("#batch-all");
  if (allBtn) allBtn.textContent = list.length && list.every((c) => selectedCids.has(c.id)) ? "取消全选" : "全选";
  $("#conv-batch")?.classList.toggle("hidden", !selectMode);
  $("#btn-select")?.classList.toggle("active", selectMode);
  if (state.conversations.length === 0) {
    box.innerHTML = `<div style="padding:24px;text-align:center;color:var(--ink-3);font-size:12px;">还没对话，发条消息开始吧</div>`;
  }
}

$("#chat-search")?.addEventListener("input", renderChatList);
function startNewChat() {
  state.currentCid = null;
  state.messages = [];
  localStorage.removeItem("xin_current_cid");
  renderChat();
  $("#chat-title").textContent = "新对话";
  $("#input").focus();
}
$("#new-chat")?.addEventListener("click", startNewChat);
$("#new-chat-2")?.addEventListener("click", startNewChat);

// 标题内联重命名
$("#chat-title-edit")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const title = $("#chat-title");
  if (!title || !state.currentCid) { toast("先选一个对话", true); return; }
  title.contentEditable = "true";
  title.focus();
  // 选中全部文本
  const range = document.createRange(); range.selectNodeContents(title);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
});
$("#chat-title")?.addEventListener("blur", async function () {
  if (this.contentEditable !== "true") return;
  this.contentEditable = "false";
  const newTitle = (this.textContent || "").trim();
  if (!newTitle || !state.currentCid) return;
  try {
    await api(`/api/conversations/${state.currentCid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: newTitle }),
    });
    await loadConversations();
  } catch (e) { toast("重命名失败：" + e.message, true); }
});
$("#chat-title")?.addEventListener("keydown", function (e) {
  if (e.key === "Enter") { e.preventDefault(); this.blur(); }
  if (e.key === "Escape") { this.textContent = state.conversations.find((c) => c.id === state.currentCid)?.title || "对话"; this.blur(); }
});
$("#btn-clear-chat")?.addEventListener("click", async () => {
  if (!state.currentCid) { toast("先发条消息吧"); return; }
  if (!confirm("确定清空这个对话？")) return;
  try {
    await api(`/api/conversations/${state.currentCid}/clear`, { method: "POST" });
    state.messages = [];
    renderChat();
    toast("已清空");
  } catch (e) { toast("清不了：" + e.message, true); }
});
$("#btn-select")?.addEventListener("click", () => setSelectMode(!selectMode));
$("#batch-cancel")?.addEventListener("click", () => setSelectMode(false));
$("#batch-all")?.addEventListener("click", () => {
  const list = visibleConversations();
  const all = list.length > 0 && list.every((c) => selectedCids.has(c.id));
  selectedCids.clear();
  if (!all) list.forEach((c) => selectedCids.add(c.id));
  renderChatList();
});
$("#batch-pin")?.addEventListener("click", () => {
  if (!selectedCids.size) { toast("先选中对话"); return; }
  const pins = JSON.parse(localStorage.getItem("xin_pinned_cids") || "[]");
  const ids = [...selectedCids];
  const merged = [...ids, ...pins.filter((id) => !selectedCids.has(id))];
  localStorage.setItem("xin_pinned_cids", JSON.stringify(merged));
  setSelectMode(false);
  loadConversations();
  toast(`已置顶 ${ids.length} 个对话`);
});
$("#batch-del")?.addEventListener("click", async () => {
  if (!selectedCids.size) { toast("先选中对话"); return; }
  if (!confirm(`确定删除选中的 ${selectedCids.size} 个对话？删除后无法恢复。`)) return;
  const ids = [...selectedCids];
  for (const id of ids) {
    try { await api(`/api/conversations/${id}`, { method: "DELETE" }); } catch {}
  }
  if (state.currentCid && selectedCids.has(state.currentCid)) {
    state.currentCid = null;
    state.messages = [];
    localStorage.removeItem("xin_current_cid");
    $("#chat-title").textContent = "与 X 的对话";
    renderChat();
  }
  setSelectMode(false);
  await loadConversations();
  toast(`已删除 ${ids.length} 个对话`);
});

async function openConversation(cid) {
  state.currentCid = cid;
  localStorage.setItem("xin_current_cid", cid);
  try {
    const data = await api(`/api/conversations/${cid}/messages`);
    state.messages = data.messages || [];
    const conv = state.conversations.find((c) => c.id === cid);
    $("#chat-title").textContent = conv?.title || "对话";
  } catch { state.messages = []; }
  renderChat();
  renderChatList();
}

/* ==========================================================================
   聊天渲染
   ========================================================================== */
function bindSidebar() { /* 上面绑定了 */ }

function renderChat() {
  const body = $("#chat-body");
  // 先把 chat-empty 拿出来单独处理，不要 innerHTML 整个清掉
  const emptyEl = body.querySelector("#chat-empty");
  // 移除所有消息（保留 chat-empty）
  body.querySelectorAll(".msg, .msg-files").forEach((n) => n.remove());

  if (!state.messages.length) {
    if (emptyEl) {
      emptyEl.classList.remove("hidden");
      // 确保它是 body 的第一个子元素
      if (emptyEl.parentElement !== body) body.prepend(emptyEl);
      // 重绑 chip 点击
      emptyEl.querySelectorAll(".chip").forEach((c) => {
        c.onclick = () => { $("#input").value = c.dataset.text; sendMessage(); };
      });
    }
    return;
  }

  // 有消息就隐藏空状态
  if (emptyEl) emptyEl.classList.add("hidden");

  [...state.messages].sort((a, b) => {
    const at = a.created_at || a.id || 0;
    const bt = b.created_at || b.id || 0;
    return String(at).localeCompare(String(bt));
  }).forEach((m) => {
    if (m.role === "user") body.appendChild(renderUserMsg(m));
    else if (m.role === "assistant") body.appendChild(renderAiMsg(m));
  });
  body.scrollTop = body.scrollHeight;
}

function renderUserMsg(m) {
  const div = document.createElement("div");
  div.className = "msg user";
  // images 可能是后端返回的数组、也可能是前端自己存的 JSON 字符串
  let imgs = m.images || [];
  if (typeof imgs === "string") { try { imgs = JSON.parse(imgs); } catch { imgs = []; } }
  if (!Array.isArray(imgs)) imgs = [];
  div.innerHTML = `
    <div class="msg-avatar">我</div>
    <div class="msg-col">
      ${(m.content || "").trim() ? `<div class="msg-bubble">${esc(m.content)}</div>` : ""}
      ${imgs.length ? `<div class="msg-imgs">${imgs.map((i) => `<img src="${i}" alt="">`).join("")}</div>` : ""}
    </div>
  `;
  return div;
}

function renderAiMsg(m) {
  const div = document.createElement("div");
  div.className = "msg ai";
  const meta = (() => {
    let v = m.meta || {};
    if (typeof v === "string") { try { v = JSON.parse(v); } catch { v = {}; } }
    if (typeof v !== "object" || v === null) v = {};
    return v;
  })();
  const regenTag = meta.regenerated ? `<div class="msg-reg-tag">♻️ 已重新生成</div>` : "";

  div.innerHTML = `
    <div class="msg-avatar"><img src="assets/logo.png" alt=""></div>
    <div class="msg-col" data-mid="${m.id}">
      <div class="msg-bubble">${renderMarkdown(m.content || "")}</div>
      ${regenTag}
      <div class="msg-actions">
  <button class="msg-act copy-act" title="复制">📋</button>
  <button class="msg-act up-act" title="👍 点赞">👍</button>
  <button class="msg-act down-act" title="👎 点踩">👎</button>
  <button class="msg-act regen-act" title="♻️ 重新生成">♻️</button>
  <button class="msg-act more" title="更多">⋯
    <div class="more-menu hidden">
      <div class="more-item" data-action="copy-md">📋 复制 Markdown</div>
      <div class="more-item" data-action="tts">🔊 朗读</div>
    </div>
  </button>
</div>
    </div>
  `;

  // 绑定操作
  const col = div.querySelector(".msg-col");
  const bubble = div.querySelector(".msg-bubble");
  const actions = div.querySelector(".msg-actions");

  actions.querySelector(".copy-act").onclick = async () => {
    try { await navigator.clipboard.writeText(m.content || ""); toast("已复制 ✓"); }
    catch { toast("复制失败", true); }
  };
  actions.querySelector(".up-act").onclick = async () => {
    const btn = actions.querySelector(".up-act");
    const was = btn.classList.contains("active");
    actions.querySelectorAll(".msg-act").forEach((b) => b.classList.remove("active", "up", "down"));
    if (!was) {
      btn.classList.add("active", "up");
      try { await api(`/api/messages/${m.id}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vote: "up" }),
      }); } catch {}
      toast("👍 已收到");
    }
  };
  actions.querySelector(".down-act").onclick = () => openDownPanel(col, m);
  actions.querySelector(".regen-act").onclick = () => regenerateMessage(m.id, bubble);

  const moreBtn = actions.querySelector(".more");
  const moreMenu = actions.querySelector(".more-menu");
  moreBtn.onclick = (e) => { e.stopPropagation(); moreMenu.classList.toggle("hidden"); };
  document.addEventListener("click", () => moreMenu.classList.add("hidden"), { once: true });
  moreMenu.querySelectorAll(".more-item").forEach((it) => {
    it.onclick = () => handleMoreAction(it.dataset.action, m);
  });

  return div;
}

/* ---------- Markdown 渲染（最小实现） ---------- */
function renderMarkdown(text) {
  if (!text) return "";
  let out = esc(text);
  // 代码块
  out = out.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) => `<pre style="background:rgba(0,0,0,.06);padding:12px;border-radius:8px;font-family:monospace;font-size:12.5px;overflow-x:auto;margin:8px 0;"><code>${esc(code)}</code></pre>`);
  // 行内代码
  out = out.replace(/`([^`]+)`/g, `<code style="background:rgba(0,0,0,.06);padding:1px 5px;border-radius:4px;font-family:monospace;font-size:12.5px;">$1</code>`);
  // 粗体/斜体
  out = out.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  out = out.replace(/\*([^*]+)\*/g, "<i>$1</i>");
  // 列表
  out = out.replace(/^- (.+)$/gm, "• $1");
  return out;
}

/* ---------- 点踩面板 ---------- */
function openDownPanel(col, m) {
  // 关掉之前的
  col.querySelectorAll(".down-panel").forEach((p) => p.remove());
  const actBtn = col.querySelector(".down-act");
  actBtn.classList.add("active", "down");

  const options = ["内容不准确", "没理解我意思", "回复太长", "回复太简短", "语气不合适", "表达方式不喜欢", "没解决问题", "其他"];
  const panel = document.createElement("div");
  panel.className = "down-panel";
  panel.innerHTML = `
    <div class="down-panel-title">👎 选个原因（可多选）</div>
    <div class="down-panel-options">${options.map((o) => `<button data-r="${o}">${o}</button>`).join("")}</div>
    <div class="down-panel-actions">
      <button class="btn primary submit">提交并重新生成</button>
      <button class="btn ghost cancel">取消</button>
    </div>
  `;
  col.appendChild(panel);

  const selected = new Set();
  panel.querySelectorAll(".down-panel-options button").forEach((b) => {
    b.onclick = () => {
      const r = b.dataset.r;
      if (selected.has(r)) { selected.delete(r); b.classList.remove("selected"); }
      else { selected.add(r); b.classList.add("selected"); }
    };
  });
  panel.querySelector(".cancel").onclick = () => {
    panel.remove(); actBtn.classList.remove("active", "down");
  };
  panel.querySelector(".submit").onclick = async () => {
    if (!selected.size) { toast("至少选一个原因哦", true); return; }
    panel.querySelector(".submit").disabled = true;
    panel.querySelector(".submit").textContent = "让模型改中...";
    try {
      await api(`/api/messages/${m.id}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vote: "down", reasons: Array.from(selected) }),
      });
      await regenerateMessage(m.id, col.querySelector(".msg-bubble"), Array.from(selected));
      panel.remove();
    } catch (e) { toast("失败：" + e.message, true); }
  };
}

/* ---------- 重生成 ---------- */
async function regenerateMessage(mid, bubble, extraReasons) {
  const resp = await fetch(`/api/messages/${mid}/regenerate`, { method: "POST" });
  if (!resp.ok) { toast("重生成失败", true); return; }
  // 流式处理
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "", text = "", newMid = null;
  state.streaming = true;
  $("#btn-send").disabled = true;
  $("#btn-stop").classList.remove("hidden");

  // 在原气泡后面插入对比框
  const col = bubble.parentElement;
  let compareBox = col.querySelector(".compare-box");
  if (!compareBox) {
    compareBox = document.createElement("div");
    compareBox.className = "compare-box";
    compareBox.innerHTML = `
      <div class="compare-row original"><div class="compare-tag original">ORIGINAL</div><div class="compare-body"></div></div>
      <div class="compare-row revised"><div class="compare-tag revised">REVISED ← 新</div><div class="compare-body typing">生成中...</div></div>
      <div class="compare-choose">
        <button class="btn ghost" data-c="original">👈 保留原版</button>
        <button class="btn primary" data-c="revised">✨ 用新版</button>
      </div>
    `;
    bubble.insertAdjacentElement("afterend", compareBox);
    // 把原版内容塞进去
    const origContent = state.messages.find((x) => x.id === mid)?.content || "";
    compareBox.querySelector(".compare-row.original .compare-body").innerHTML = renderMarkdown(origContent);
  }
  const revisedBody = compareBox.querySelector(".compare-row.revised .compare-body");
  revisedBody.className = "compare-body";
  revisedBody.innerHTML = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const payload = JSON.parse(line.slice(6));
          if (payload.type === "meta") newMid = payload.message_id;
          else if (payload.type === "token") {
            text += payload.text;
            revisedBody.innerHTML = renderMarkdown(text);
          } else if (payload.type === "error") { toast(payload.message, true); }
        }
      }
    }
  } catch (e) { toast("流中断：" + e.message, true); }

  state.streaming = false;
  $("#btn-send").disabled = false;
  $("#btn-stop").classList.add("hidden");

  // 选择按钮
  compareBox.querySelectorAll(".compare-choose .btn").forEach((b) => {
    b.onclick = async () => {
      const choice = b.dataset.c;
      if (newMid && choice === "revised") {
        state.messages.push({ id: newMid, role: "assistant", content: text, conversation_id: state.currentCid });
      }
      compareBox.querySelectorAll(".compare-choose").forEach((el) => el.remove());
      compareBox.querySelectorAll(".compare-tag").forEach((t) => t.style.opacity = ".5");
      bubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
      try {
        await api(`/api/feedbacks`, { method: "GET" }); // 确保路由注册
        const fmid = compareBox.closest(".msg-col")?.dataset?.mid;
      } catch {}
      toast(choice === "revised" ? "已采纳新版 ✨" : "保留原版");
      // 刷新对话
      await openConversation(state.currentCid);
    };
  });
}

/* ---------- 更多菜单动作 ---------- */
async function handleMoreAction(action, m) {
  if (action === "copy-md") {
    try { await navigator.clipboard.writeText(m.content || ""); toast("已复制"); } catch {}
  } else if (action === "tts") {
    if (!("speechSynthesis" in window)) { toast("浏览器不支持朗读", true); return; }
    const u = new SpeechSynthesisUtterance(m.content || "");
    u.lang = "zh-CN"; u.rate = 1; speechSynthesis.speak(u);
    toast("🔊 朗读中...");
  } else if (action === "export") {
    const blob = new Blob([m.content || ""], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `XIN_${m.id}.txt`; a.click();
    URL.revokeObjectURL(url);
    toast("已下载");
  } else if (action === "info") {
    let meta = m.meta || {};
    if (typeof meta === "string") { try { meta = JSON.parse(meta); } catch { meta = {}; } }
    if (typeof meta !== "object" || meta === null) meta = {};
    const info = `模型：${m.model || (state.settings?.chat_model) || "?"}\n时间：${m.created_at || ""}\nMeta：${JSON.stringify(meta, null, 2)}`;
    openModal("消息信息", `<pre style="white-space:pre-wrap;font-size:12px;">${esc(info)}</pre>`);
  } else if (action === "del") {
    if (!confirm("删除这条回复？")) return;
    toast("（暂不删单条，后端未开放）");
  }
}

/* ==========================================================================
   SSE 对话发送
   ========================================================================== */
function bindChat() {
  const ta = $("#input");
  ta?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(); }
  });
  ta?.addEventListener("input", () => {
    ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  });

  $("#btn-send")?.addEventListener("click", sendMessage);
  $("#btn-stop")?.addEventListener("click", () => state.abort?.abort());
  $("#btn-think")?.addEventListener("click", (e) => {
    e.currentTarget.classList.toggle("active");
  });
  $("#btn-attach")?.addEventListener("click", () => $("#file-picker").click());
  $("#file-picker")?.addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
  $("#btn-image")?.addEventListener("click", () => $("#image-picker").click());
  $("#image-picker")?.addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

  // 拖放：整个对话页接收，输入框上高亮
  const composer = $(".composer");
  const chatPage = $("#page-chat");
  if (composer && chatPage) {
    const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
    ["dragenter", "dragover"].forEach((ev) => chatPage.addEventListener(ev, (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      if (e.target.closest?.(".composer")) composer.classList.add("drag-over");
    }));
    chatPage.addEventListener("dragleave", (e) => {
      if (!composer.contains(e.relatedTarget)) composer.classList.remove("drag-over");
    });
    chatPage.addEventListener("drop", (e) => {
      if (!e.dataTransfer?.files?.length) return;
      e.preventDefault();
      composer.classList.remove("drag-over");
      addFiles(e.dataTransfer.files);
    });
  }

  // 附件行移除
  $("#attach-row")?.addEventListener("click", (e) => {
    const btn = e.target.closest?.("button"); if (!btn) return;
    const card = btn.closest(".file-chip, .attach-item");
    if (!card) return;
    const idx = Array.from($("#attach-row").children).indexOf(card);
    if (idx < 0) return;
    state.attachments.splice(idx, 1); renderAttachments();
  });

  // Inspire / 每日一眼（已移除按钮，保留 prompt 数组备用）
  const INSPIRE_PROMPTS = [
    "写一首关于雨的短诗",
    "帮我润色：这段话太生硬了...",
    "给我 3 个提高专注力的方法",
    "用大白话解释一下什么是量子纠缠",
    "编一个关于咖啡的冷知识",
    "今天情绪有点低落，给我讲个小笑话",
    "帮我写一封辞职信，优雅一点",
    "推荐 5 本改变过人生的书",
    "用 Python 写个快速排序，并解释每行",
    "模拟面试：你是产品经理，我是面试官",
    "翻译一下：To be or not to be, that is the question.",
    "帮我想 10 个有趣的创业点子",
    "如果你有一天变成猫，你会做什么？",
    "给我一份一周早餐计划，要营养均衡",
    "解释一下什么是「熵」，用奶茶举例",
  ];

  // Emoji 弹窗
  const EMOJIS = ["😀","😂","🥰","😎","🤔","😴","😭","🥳","👍","👎","❤️","🔥","✨","🎉","💡","📝","📌","📎","💬","🌙","☀️","🌈","🍀","☕","🍜","🎵","💻","📱","🧠","💭","🚀","🏆"];
  $("#btn-emoji")?.addEventListener("click", (e) => {
    e.stopPropagation();
    let popup = $(".emoji-popup");
    if (!popup) {
      popup = document.createElement("div");
      popup.className = "emoji-popup hidden";
      popup.innerHTML = EMOJIS.map((em) => `<button type="button">${em}</button>`).join("");
      $(".composer")?.appendChild(popup);
      popup.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const ta = $("#input"); if (!ta) return;
          const pos = ta.selectionStart ?? ta.value.length;
          ta.value = ta.value.slice(0, pos) + b.textContent + ta.value.slice(pos);
          ta.selectionStart = ta.selectionEnd = pos + b.textContent.length;
          ta.focus();
          popup.classList.add("hidden");
        });
      });
    }
    popup.classList.toggle("hidden");
  });
  // 点击外部关闭 emoji 弹窗
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".emoji-popup") && !e.target.closest("#btn-emoji")) {
      $(".emoji-popup")?.classList.add("hidden");
    }
  });
}

function isDocFile(f) {
  const ext = (f.name || "").toLowerCase().match(/\.[^.]+$/)?.[0] || "";
  return [".pdf", ".docx", ".xlsx", ".xls", ".txt", ".csv", ".md"].includes(ext);
}
async function addFiles(fileList) {
  for (const file of Array.from(fileList || [])) {
    const isImg = file.type.startsWith("image/");
    const isDoc = !isImg && isDocFile(file);
    if (!isImg && !isDoc) continue;
    const curImg = state.attachments.filter((a) => a.kind !== "doc").length;
    const curDoc = state.attachments.filter((a) => a.kind === "doc").length;
    if (isImg && curImg >= 4) { toast("最多 4 张图片"); break; }
    if (isDoc && curDoc >= 5) { toast("最多 5 个文档"); break; }
    if (file.size > (isImg ? 8 * 1024 * 1024 : 50 * 1024 * 1024)) {
      toast(`${file.name} 超过限制`, true); continue;
    }
    const dataUrl = await new Promise((res) => {
      const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(file);
    });
    state.attachments.push({ data: dataUrl, name: file.name, kind: isImg ? "image" : "doc" });
  }
  renderAttachments();
}
function renderAttachments() {
  const box = $("#attach-row"); if (!box) return;
  box.innerHTML = "";
  state.attachments.forEach((a) => {
    const div = document.createElement("div");
    if (a.kind === "doc") {
      const ext = ((a.name.match(/\.[^.]+$/)?.[0] || "")).toLowerCase().slice(1) || "file";
      const sz = fmtSize(parseInt(a.data.match(/^data:[^;]+;base64,/)?.[0]?.length || "0"));
      div.className = "file-chip";
      div.innerHTML = `
        <div class="file-chip-icon ${ext}">${esc(ext.toUpperCase())}</div>
        <div class="file-chip-info">
          <div class="file-chip-name">${esc(a.name)}</div>
          <div class="file-chip-meta">${a.size ? fmtSize(a.size) : ""}</div>
        </div>
        <button title="移除">×</button>
      `;
    } else {
      div.className = "attach-item";
      div.innerHTML = `<div class="img-card"><img src="${a.data}"><button title="移除">×</button></div>`;
    }
    box.appendChild(div);
  });
  // 只隐藏附件行本身，绝不能动 .composer（否则发消息时输入框会消失）
  box.style.display = state.attachments.length ? "" : "none";
}

/* 从服务端重新拉取当前对话消息并重绘：保证用户消息 + AI 回复都完整保留 */
async function syncCurrentMessages() {
  if (!state.currentCid) return;
  try {
    const data = await api(`/api/conversations/${state.currentCid}/messages`);
    state.messages = data.messages || [];
    renderChat();
    loadConversations().catch(() => {});
  } catch (e) {
    console.warn("同步消息失败", e);
  }
}

async function sendMessage() {
  // 兜底：如果之前的 streaming 卡太久，强制重置
  if (state.streaming) {
    if (!state._streamStart || (Date.now() - state._streamStart) > 180000) {
      console.warn("streaming 超时，强制重置");
      state.streaming = false; state.abort = null;
      $("#btn-send").disabled = false;
      $("#btn-stop").classList.add("hidden");
    } else {
      toast("AI 正在回复中，稍等～", false);
      return;
    }
  }
  const ta = $("#input");
  const text = ta.value.trim();
  const images = state.attachments.filter((a) => a.kind !== "doc").map((i) => i.data);
  const docs = state.attachments.filter((a) => a.kind === "doc").map((d) => d.data);
  if (!text && !images.length && !docs.length) return;

  // UI
  $("#btn-send").disabled = true;
  $("#btn-stop").classList.remove("hidden");
  state.streaming = true; state._streamStart = Date.now();
  ta.value = ""; ta.style.height = "auto";
  localStorage.removeItem("xin_input_draft");

  // 用户消息
  appendUserNote(text, images, docs);
  state.attachments = []; renderAttachments();

  // 思考状态
  const aiDiv = appendAiTyping();

  try {
    const ctrl = new AbortController(); state.abort = ctrl;
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: text,
        images, documents: docs,
        think: $("#btn-think")?.classList.contains("active") || false,
        conversation_id: state.currentCid,
      }),
      signal: ctrl.signal,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "", content = "", mid = null, doneMeta = null, savedMessage = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        for (const raw of chunk.split("\n")) {
          const line = raw.trim();
          if (!line.startsWith("data: ")) continue;
          try {
            const payload = JSON.parse(line.slice(6));
            if (payload.type === "meta") {
              doneMeta = payload;
              if (payload.user_message?.id) mid = payload.user_message.id;
              // 新对话时切换标题
              if (!state.currentCid) {
                state.currentCid = payload.conversation_id;
                localStorage.setItem("xin_current_cid", state.currentCid);
                loadConversations().catch(() => {});
              }
              const titleEl = $("#chat-title");
              if (titleEl && payload.title) titleEl.textContent = payload.title;
            } else if (payload.type === "token") {
              content += payload.text;
              updateAiTyping(aiDiv, content);
            } else if (payload.type === "done") {
              if (payload.message) savedMessage = payload.message;
              updateAiTyping(aiDiv, content, true);
            } else if (payload.type === "error") {
              toast(payload.message, true);
            }
          } catch (err) {
            // SSE 分块可能切在 JSON 中间，跳过本 chunk 继续累积
            console.warn("SSE chunk parse skip:", err.message.slice(0,60));
          }
        }
      }
    }

    // 用服务端结果校准本地消息列表，避免后续重绘时把用户问题或 AI 回复弄丢
    if (savedMessage || mid) await syncCurrentMessages();
  } catch (e) {
    if (e.name === "AbortError") toast("已停止");
    else toast("发送失败：" + e.message, true);
    aiDiv.querySelector(".msg-bubble").textContent = "（出错了：" + e.message + "）";
  } finally {
    state.streaming = false; state.abort = null; state._streamStart = null;
    $("#btn-send").disabled = false;
    $("#btn-stop").classList.add("hidden");
    // 强制恢复 composer 可见（防止意外被隐藏）
    const cp = $(".composer"); if (cp) { cp.style.display = ""; cp.classList.remove("hidden"); }
  }
}

function appendUserNote(text, images, docs) {
  // 本地先插入一条带时间戳的用户消息，保证重绘时顺序正确（服务端已同时持久化）
  const ts = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const createdAt = `${ts.getFullYear()}-${pad(ts.getMonth() + 1)}-${pad(ts.getDate())} ${pad(ts.getHours())}:${pad(ts.getMinutes())}:${pad(ts.getSeconds())}`;
  state.messages.push({ id: "local-" + ts.getTime(), role: "user", content: text, images: images || [], created_at: createdAt });
  renderChat();
  // 显示 docs 提示
  if (docs && docs.length) {
    const lastMsg = $("#chat-body").lastElementChild;
    const note = document.createElement("div");
    note.className = "msg-files";
    note.style.alignSelf = "flex-end";
    note.textContent = `📎 附带 ${docs.length} 个文档`;
    $("#chat-body").appendChild(note);
  }
  $("#chat-body").scrollTop = $("#chat-body").scrollHeight;
}
function appendAiTyping() {
  const div = document.createElement("div");
  div.className = "msg ai";
  div.innerHTML = `
    <div class="msg-avatar"><img src="assets/logo.png" alt=""></div>
    <div class="msg-col">
      <div class="msg-bubble"><div class="typing"><span></span><span></span><span></span></div></div>
    </div>
  `;
  $("#chat-body").appendChild(div);
  $("#chat-body").scrollTop = $("#chat-body").scrollHeight;
  return div;
}
function updateAiTyping(div, text, done = false) {
  const bubble = div.querySelector(".msg-bubble");
  bubble.innerHTML = renderMarkdown(text);
  $("#chat-body").scrollTop = $("#chat-body").scrollHeight;
  if (done) {
    // 替换为完整的 ai 消息（带操作栏）
    const fakeMsg = { id: div.querySelector(".msg-col")?.dataset?.mid || "pending", role: "assistant", content: text, meta: "{}" };
    const newEl = renderAiMsg(fakeMsg);
    div.replaceWith(newEl);
  }
}

/* ==========================================================================
   记忆 / 人格 / 模型 / 设置 / PDF / 反馈（快速绑）
   ========================================================================== */
function bindMemory() {
  $("#memory-search")?.addEventListener("input", renderMemories);
  $("#memory-filter")?.addEventListener("change", renderMemories);
  $("#btn-memory-extract")?.addEventListener("click", async () => {
    try { await api("/api/memories/extract", { method: "POST" }); toast("已触发抽取"); }
    catch (e) { toast(e.message, true); }
    setTimeout(async () => { state.memories = await api("/api/memories"); renderMemories(); }, 1500);
  });
  $("#btn-memory-clean")?.addEventListener("click", async () => {
    try { await api("/api/memories/clean", { method: "POST" }); toast("已整理"); } catch (e) { toast(e.message, true); }
    state.memories = await api("/api/memories"); renderMemories();
  });
  $("#btn-memory-new")?.addEventListener("click", () => {
    const content = prompt("记点什么？"); if (!content) return;
    try {
      api("/api/memories", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content, kind: "fact" }) });
      toast("记下了 ✓");
      setTimeout(async () => { state.memories = await api("/api/memories"); renderMemories(); }, 500);
    } catch {}
  });
}
function renderMemories() {
  const box = $("#memory-list"); if (!box) return;
  const f = $("#memory-filter")?.value || "";
  const s = ($("#memory-search")?.value || "").toLowerCase();
  box.innerHTML = "";
  state.memories.filter((m) => (!f || m.kind === f) && (!s || (m.content || "").toLowerCase().includes(s)))
    .forEach((m) => {
      const d = document.createElement("div"); d.className = "memory-item";
      d.innerHTML = `
        <div class="memory-item-head">
          <span class="memory-item-text">${esc(m.content)}</span>
          <span style="font-size:10px;color:var(--ink-3);">${m.kind || ""} · ⭐${m.importance || 3}</span>
        </div>
        <div class="memory-item-meta">${fmtDate(m.created_at || "")}</div>
      `;
      box.appendChild(d);
    });
}

async function loadPersona() {
  if (state.persona) return;
  try {
    const d = await api("/api/persona"); state.persona = d.persona;
  } catch { return; }
  $("#p-ai_name").value = state.persona.ai_name || "";
  $("#p-user_name").value = state.persona.user_name || "";
  $("#p-personality").value = state.persona.personality || "";
  $("#p-tone").value = state.persona.tone || "";
  $("#p-boundaries").value = state.persona.boundaries || "";
  $("#p-greeting").value = state.persona.greeting || "";
}
function bindPersona() {
  $("#btn-persona-save")?.addEventListener("click", async () => {
    const payload = {
      ai_name: $("#p-ai_name").value,
      user_name: $("#p-user_name").value,
      personality: $("#p-personality").value,
      tone: $("#p-tone").value,
      boundaries: $("#p-boundaries").value,
      greeting: $("#p-greeting").value,
    };
    try { await api("/api/persona", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); toast("已保存 ✓"); }
    catch (e) { toast(e.message, true); }
  });
  $("#btn-persona-reset")?.addEventListener("click", async () => {
    if (!confirm("恢复默认人格？")) return;
    try { await api("/api/persona/reset", { method: "POST" }); state.persona = null; await loadPersona(); toast("已恢复"); }
    catch (e) { toast(e.message, true); }
  });
}

function renderModels() {
  const box = $("#model-list"); if (!box) return;
  const cur = state.settings?.chat_model;
  box.innerHTML = "";
  (state.models || []).forEach((m) => {
    const d = document.createElement("div"); d.className = "model-item";
    const isCur = m.name === cur;
    d.innerHTML = `
      <div class="model-item-head">
        <div>
          <div class="model-item-name">${esc(m.name)}${isCur ? " <span style='color:#10b981;font-size:11px;'>● 当前</span>" : ""}</div>
          <div class="model-item-meta">${(m.params || "")} · ${(m.size_gb || "")} · ${(m.family || "")}</div>
        </div>
        <div>
          ${!isCur ? `<button class="btn ghost" data-sel="${m.name}">选用</button>` : ""}
        </div>
      </div>
    `;
    d.querySelector("[data-sel]")?.addEventListener("click", async () => {
      try { await api(`/api/models/${m.name}/use`, { method: "POST" }); state.settings.chat_model = m.name; renderModels(); fillModelSelect(); toast("已切换"); }
      catch (e) { toast(e.message, true); }
    });
    box.appendChild(d);
  });
}
function fillModelSelect() {
  const sel = $("#s-chat-model"); if (!sel) return;
  sel.innerHTML = "";
  const list = Array.isArray(state.models) ? state.models : (state.models?.items || []);
  list.forEach((m) => {
    const o = document.createElement("option"); o.value = m.name; o.textContent = m.name;
    if (m.name === state.settings?.chat_model) o.selected = true;
    sel.appendChild(o);
  });
  // 也同步到 chat 页右下角
  const ind = $("#model-indicator");
  if (ind) ind.textContent = state.settings?.chat_model || "";
}
function bindModels() {
  $("#btn-model-pull")?.addEventListener("click", async () => {
    const name = $("#model-pull-name").value.trim(); if (!name) return;
    try { await api(`/api/models/${encodeURIComponent(name)}/pull`, { method: "POST" }); toast(`已开始拉 ${name}`); }
    catch (e) { toast(e.message, true); }
  });
}

async function loadSettingsPage() {
  fillModelSelect();
  // 直接从 API 拉最新设置，不依赖可能过时的 state.settings
  let s = {};
  try {
    s = await api("/api/settings");
    state.settings = s;
  } catch (e) {
    s = state.settings || {};
  }
  $("#s-temperature").value = s.temperature ?? 0.85;
  $("#s-num_ctx").value = s.num_ctx ?? 8192;
  // 自动记忆：后端 True 或 undefined → checked，只有显式 false 才关
  const memBox = $("#s-memory-enabled");
  const memVal = (s.memory_enabled !== false);
  if (memBox) memBox.checked = memVal;
  state._memLoaded = memVal;   // 记录基准，保存时只有变化才提交
  // 方言开关
  const dialBox = $("#s-dialect-enabled");
  if (dialBox) dialBox.checked = (s.dialect_enabled !== false);
  // 主题按钮激活
  const themeKey = (state.theme || s.theme || "purple");
  $$("#theme-switch .t-btn").forEach((b) => b.classList.toggle("active", b.dataset.theme === themeKey));
  // 背景控件
  // 背景控件（blur/mask slider 已删除，只留 opacity）
  const blurEl = $("#bg-blur"); if (blurEl) { blurEl.value = state.bgBlur ?? 8; $("#bg-blur-val").textContent = `${blurEl.value}px`; }
  $("#bg-opacity").value = Math.round((state.bgOpacity ?? 1) * 100);
  $("#bg-opacity-val").textContent = `${$("#bg-opacity").value}%`;
  const maskEl = $("#bg-mask"); if (maskEl) { maskEl.value = Math.round((state.bgMask ?? 0.4) * 100); $("#bg-mask-val").textContent = `${maskEl.value}%`; }
  // 懒加载其他 tab 的数据
  loadPersona().catch(() => {});
  try { state.memories = await api("/api/memories"); renderMemories(); } catch {}
  try { state.feedbacks = await api("/api/feedbacks"); renderFeedbackList(); } catch {}
}
function bindSettings() {
  // 设置页 · Tab 切换
  $$(".set-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const t = tab.dataset.tab;
      $$(".set-tab").forEach((x) => x.classList.toggle("active", x === tab));
      $$(".set-panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === t));
    });
  });

  $("#theme-switch")?.addEventListener("click", (e) => {
    const b = e.target.closest(".t-btn"); if (!b) return;
    applyTheme(b.dataset.theme);
  });
  $("#bg-opacity")?.addEventListener("input", (e) => {
    state.bgOpacity = +e.target.value / 100; applyBgVars();
    $("#bg-opacity-val").textContent = e.target.value + "%";
  });
  $("#bg-blur")?.addEventListener("input", (e) => {
    state.bgBlur = +e.target.value; applyBgVars();
    $("#bg-blur-val").textContent = e.target.value + "px";
  });
  $("#bg-mask")?.addEventListener("input", (e) => {
    state.bgMask = +e.target.value / 100; applyBgVars();
    $("#bg-mask-val").textContent = e.target.value + "%";
  });
  $("#bg-upload")?.addEventListener("change", (e) => {
    const f = e.target.files?.[0]; if (!f) return;
    const r = new FileReader(); r.onload = () => { state.bgUrl = r.result; applyBgVars(); }; r.readAsDataURL(f);
  });
  $("#s-dialect-enabled")?.addEventListener("change", (e) => {
    const val = e.target.checked;
    state.dialectEnabled = val;
  });
  $("#bg-reset")?.addEventListener("click", () => {
    state.bgUrl = null; state.bgOpacity = 0.8; state.bgBlur = 8; state.bgMask = 0.4;
    applyBgVars();
    const opEl = $("#bg-opacity"); if (opEl) { opEl.value = 80; $("#bg-opacity-val").textContent = "80%"; }
    const blEl = $("#bg-blur"); if (blEl) { blEl.value = 8; $("#bg-blur-val").textContent = "8px"; }
    const mkEl = $("#bg-mask"); if (mkEl) { mkEl.value = 40; $("#bg-mask-val").textContent = "40%"; }
  });
  $("#btn-settings-save")?.addEventListener("click", async () => {
    const memoryTopkEl = $("#s-memory-topk");
    const payload = {
      temperature: parseFloat($("#s-temperature").value),
      num_ctx: parseInt($("#s-num_ctx").value, 10),
      memory_top_k: memoryTopkEl ? parseInt(memoryTopkEl.value, 10) : (state.settings?.memory_top_k ?? 6),
      chat_model: $("#s-chat-model").value || state.settings?.chat_model,
      theme: state.theme,
      bg: { bg_url: state.bgUrl, bg_opacity: state.bgOpacity, bg_blur: state.bgBlur, bg_mask: state.bgMask },
      dialect_enabled: $("#s-dialect-enabled")?.checked ?? state.dialectEnabled ?? true,
    };
    // 关键：只有用户真的动过「自动记忆」开关才提交，避免被误覆盖成 false
    const memBox = $("#s-memory-enabled");
    if (memBox) {
      const nowChecked = !!memBox.checked;
      if (state._memLoaded === undefined || nowChecked !== state._memLoaded) {
        payload.memory_enabled = nowChecked;
        state._memLoaded = nowChecked;
      }
    }
    try {
      await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      state.settings = { ...state.settings, ...payload };
      fillModelSelect(); toast("已保存 ✓");
    } catch (e) { toast(e.message, true); }
  });
  $("#btn-export")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/export");
      const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob); const a = document.createElement("a");
      a.href = url; a.download = `XIN_backup_${Date.now()}.json`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { toast(e.message, true); }
  });
}
function applyTheme(name) {
  state.theme = name || "purple";
  document.body.className = `theme-${state.theme}`;
}
function applyBg(data) {
  if (!data) return;
  state.bgUrl = data.bg_url || null;
  state.bgOpacity = data.bg_opacity ?? 0.8;
  state.bgBlur = data.bg_blur ?? 8;
  state.bgMask = data.bg_mask ?? 0.4;
  applyBgVars();
}
function applyBgVars() {
  const layer = $("#bg-layer"); if (!layer) return;
  const mask = $(".bg-mask");
  if (state.bgUrl) {
    // 自定义背景：清晰优先 —— 绝不模糊、绝不加暗色遮罩
    layer.style.backgroundImage = `url("${state.bgUrl}")`;
    layer.style.opacity = 1;
    layer.style.filter = "none";
    if (mask) mask.style.background = "transparent";
    document.querySelector(".wallpaper")?.setAttribute("data-custom", "true");
    document.querySelector(".app")?.classList.add("has-custom-wallpaper");
    document.querySelector(".app")?.setAttribute("data-sharp-wallpaper", "1");
    return;
  }
  // 无自定义背景 → 用主题预设渐变
  const idxMap = { purple: 0, blue: 2, green: 3, warm: 4, dark: 1 };
  layer.style.backgroundImage = PRESET_BGS[idxMap[state.theme] || 0];
  layer.style.opacity = state.bgOpacity;
  layer.style.filter = `blur(${state.bgBlur ?? 8}px)`;
  if (mask) mask.style.background = `rgba(10,6,28,${state.bgMask ?? 0.4})`;
  document.querySelector(".wallpaper")?.removeAttribute("data-custom");
  document.querySelector(".app")?.classList.remove("has-custom-wallpaper");
  document.querySelector(".app")?.removeAttribute("data-sharp-wallpaper");
}

/* PDF 工具 */
function bindPdf() {
  const drop = $("#pdf-drop"); const fileInput = $("#pdf-file");
  if (!drop || !fileInput) return;
  drop.addEventListener("click", () => fileInput.click());
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("dragging"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files?.[0]; if (f) handlePdfFile(f); });
  fileInput.addEventListener("change", (e) => { const f = e.target.files?.[0]; if (f) handlePdfFile(f); });
  $("#pdf-choose")?.addEventListener("click", () => fileInput.click());
  // 重置为「等待选择」状态（再来一个 / 换文件共用）
  const resetPdf = () => {
    state.pdfFile = null;
    state.pdfPreviewImages = [];
    $("#pdf-drop")?.classList.remove("hidden");
    $("#pdf-preview")?.classList.add("hidden");
    $("#pdf-result")?.classList.add("hidden");
    const fn = $("#pdf-filename"); if (fn) fn.textContent = "未选择文件";
    const pg = $("#pdf-pages"); if (pg) pg.textContent = "拖入 PDF 或点击上传区域";
    const st = $("#pdf-status"); if (st) { st.textContent = "等待选择 PDF 文件。"; st.style.color = ""; }
    fileInput.value = "";
  };
  // 渲染格式卡片（带说明文字）
  const fmtBox = $("#pdf-formats");
  const FMT_META = {
    "word-fidelity": { icon: "📄", desc: "最接近 PDF 外观，但文字不可编辑",
      tip: "Word 图片版会把每页 PDF 渲染成高清图片放进 DOCX。它不是文字识别，只是版式保真方案。" },
    "word-editable": { icon: "📝", desc: "适合二次编辑，复杂版式可能漂移",
      tip: "Word 可编辑版会尝试把文字和段落还原成可编辑内容。复杂排版、表格和扫描页可能会有偏差。" },
    "excel": { icon: "📊", desc: "提取文本行到表格，复杂表格可能失真",
      tip: "Excel 导出按文本行写入表格。行、列结构不一定能还原成原表格式，扫描件也不会自动识别。" },
    "png": { icon: "🖼️", desc: "每页一张 PNG，无损清晰",
      tip: "PNG 图片包按页导出无损图片。适合审阅、归档、做预览，不会再生成可编辑文字。" },
    "jpeg": { icon: "🏞️", desc: "体积更小，适合分享",
      tip: "JPEG 图片包按页导出压缩图片，体积更小。适合分享和快速预览，画质为有损压缩。" },
    "txt": { icon: "📃", desc: "只提取可复制文字",
      tip: "TXT 只提取可复制文本。扫描件、纯图片 PDF 可能几乎没有内容。" },
  };
  const applyTip = (key) => {
    const tipEl = $("#pdf-mode-tip");
    if (tipEl) tipEl.textContent = (FMT_META[key] || {}).tip || "";
  };
  fetch("/api/pdf/formats").then((r) => r.json()).then((d) => {
    if (!fmtBox) return;
    (d.formats || []).forEach((f, i) => {
      const meta = FMT_META[f.key] || { icon: "📄", desc: "", tip: "" };
      const div = document.createElement("div");
      div.className = "fmt-card" + (i === 0 ? " selected" : "");
      div.dataset.fmt = f.key;
      div.innerHTML = `<div class="fmt-icon">${meta.icon}</div><div class="fmt-name">${f.label}</div><div class="fmt-sub">.${f.extension}</div><div class="fmt-desc">${meta.desc}</div>`;
      div.onclick = () => {
        state.pdfFmt = f.key;
        fmtBox.querySelectorAll(".fmt-card").forEach((c) => c.classList.toggle("selected", c === div));
        applyTip(f.key);
      };
      fmtBox.appendChild(div);
    });
    state.pdfFmt = d.formats?.[0]?.key || "word-fidelity";
    applyTip(state.pdfFmt);
  });
  // 预览分页器
  $("#pdf-prev")?.addEventListener("click", () => stepPdfPreview(-1));
  $("#pdf-next")?.addEventListener("click", () => stepPdfPreview(1));
  // 生成记录清空
  $("#pdf-clear-history")?.addEventListener("click", () => {
    localStorage.removeItem("pdfToolHistory");
    renderPdfHistory();
  });
  renderPdfHistory();

  $("#pdf-convert")?.addEventListener("click", async () => {
    if (!state.pdfFile) return;
    const btn = $("#pdf-convert"); const status = $("#pdf-status");
    if (!btn) return;
    btn.disabled = true; btn.textContent = "转换中..."; if (status) { status.textContent = ""; status.style.color = ""; }
    try {
      const form = new FormData();
      form.append("file", state.pdfFile);
      form.append("fmt", state.pdfFmt);
      const resp = await fetch("/api/pdf/convert", { method: "POST", body: form });
      if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
      const data = await resp.json();
      state.pdfResult = data;
      $("#pdf-result")?.classList.remove("hidden");
      if (status) { status.textContent = "转换完成，文件已写入生成记录。"; status.style.color = ""; }
      const t = $("#pdf-result-title"); if (t) t.textContent = `${data.format_label || ""} · ${data.file_name || ""}`;
      const m = $("#pdf-result-meta"); if (m) m.textContent = `${data.pages || 0} 页 · 用时 ${data.seconds || 0}s`;
      const a = $("#pdf-download"); if (a) { a.href = data.download_url; a.download = data.file_name || "result"; }
      addPdfHistory({
        name: data.file_name || state.pdfFile.name,
        fmt: data.format_label || state.pdfFmt,
        pages: data.pages || 0,
        url: data.download_url,
        time: new Date().toISOString(),
      });
    } catch (e) {
      if (status) { status.textContent = "失败：" + e.message; status.style.color = "#dc2626"; }
    } finally { btn.disabled = false; btn.textContent = "开始转换"; }
  });
  $("#pdf-again")?.addEventListener("click", resetPdf);
}

/* PDF 预览分页 */
function stepPdfPreview(delta) {
  const imgs = state.pdfPreviewImages || [];
  if (!imgs.length) return;
  state.pdfPreviewIdx = Math.max(0, Math.min(imgs.length - 1, (state.pdfPreviewIdx || 0) + delta));
  const img = $("#pdf-preview-img");
  if (img) img.src = imgs[state.pdfPreviewIdx];
  const ind = $("#pdf-page-ind");
  if (ind) ind.textContent = `${state.pdfPreviewIdx + 1} / ${imgs.length}`;
  const prev = $("#pdf-prev"); if (prev) prev.disabled = state.pdfPreviewIdx <= 0;
  const next = $("#pdf-next"); if (next) next.disabled = state.pdfPreviewIdx >= imgs.length - 1;
}

/* PDF 生成记录 */
function readPdfHistory() {
  try { return JSON.parse(localStorage.getItem("pdfToolHistory") || "[]"); } catch { return []; }
}
function addPdfHistory(item) {
  const list = readPdfHistory();
  list.unshift(item);
  localStorage.setItem("pdfToolHistory", JSON.stringify(list.slice(0, 10)));
  renderPdfHistory();
}
function renderPdfHistory() {
  const box = $("#pdf-history-list"); if (!box) return;
  const list = readPdfHistory();
  if (!list.length) { box.innerHTML = `<div class="pdf-history-empty">还没有转换记录</div>`; return; }
  box.innerHTML = "";
  list.forEach((h) => {
    const row = document.createElement("div"); row.className = "pdf-history-item";
    const d = h.time ? new Date(h.time) : null;
    const timeStr = d && !isNaN(d) ? `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : "";
    row.innerHTML = `<div class="pdf-history-main"><div class="pdf-history-name">${h.name || "未命名"}</div><div class="pdf-history-meta">${h.fmt || ""} · ${h.pages || 0} 页 · ${timeStr}</div></div>`;
    if (h.url) {
      const a = document.createElement("a"); a.className = "pill-btn ghost small"; a.href = h.url; a.download = h.name || "result"; a.textContent = "下载";
      row.appendChild(a);
    }
    box.appendChild(row);
  });
}

function handlePdfFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) { toast("只支持 PDF", true); return; }
  if (file.size > 100 * 1024 * 1024) { toast("超过 100MB", true); return; }
  state.pdfFile = file;
  $("#pdf-drop")?.classList.add("hidden");
  $("#pdf-result")?.classList.add("hidden");
  const st0 = $("#pdf-status"); if (st0) { st0.textContent = "正在解析预览..."; st0.style.color = ""; }
  const fn = $("#pdf-filename"); if (fn) fn.textContent = `${file.name} · ${fmtSize(file.size)}`;
  // 预览（正确的 FormData 构造）
  const fd = new FormData();
  fd.append("file", file);
  fetch("/api/pdf/preview", { method: "POST", body: fd })
    .then((r) => r.json())
    .then((d) => {
      const pagesEl = $("#pdf-pages"); if (pagesEl) pagesEl.textContent = `${d.pages || 0} 页`;
      const st1 = $("#pdf-status"); if (st1) { st1.textContent = `已解析 ${d.pages || 0} 页，选择导出方式后点「开始转换」。`; st1.style.color = ""; }
      const imgs = d.images || [];
      state.pdfPreviewImages = imgs;
      state.pdfPreviewIdx = 0;
      if (imgs.length > 0) {
        const img = $("#pdf-preview-img"); if (img) img.src = imgs[0];
        stepPdfPreview(0);
        $("#pdf-preview")?.classList.remove("hidden");
      }
    })
    .catch((e) => { console.warn("预览失败", e); });
}

/* 反馈中心 */
async function loadFeedbacks() {
  try {
    const d = await api("/api/feedbacks");
    state.feedbacks = d.items || [];
    state.feedbackStats = d.stats || {};
  } catch {}
  renderFeedbackList();
}
function renderFeedbackList() {
  const box = $("#feedback-list"); if (!box) return;
  const st = state.feedbackStats || state.feedbacks;
  if (st.up !== undefined) {
    $("#feedback-stats").textContent = `👍 ${st.up || 0}  👎 ${st.down || 0}  🎯 已选 ${st.with_choice || 0}`;
  }
  const filter = state.feedbackFilter || "all";
  box.innerHTML = "";
  state.feedbacks
    .filter((f) => filter === "all" || f.vote === filter)
    .forEach((f) => {
      const d = document.createElement("div"); d.className = "feedback-item";
      const isUp = f.vote === "up";
      d.innerHTML = `
        <div class="feedback-item-head">
          <span style="font-size:18px;">${isUp ? "👍" : "👎"}</span>
          <span style="font-size:11px;color:var(--ink-3);">${fmtDate(f.created_at)}</span>
          ${f.final_choice ? `<span style="font-size:10px;color:#10b981;margin-left:auto;">已采纳 ${f.final_choice}</span>` : ""}
        </div>
        <div class="feedback-item-q">Q: ${esc(f.user_question)}</div>
        <div class="feedback-item-a">${esc(f.ai_reply)}</div>
        ${f.reasons?.length ? `<div class="feedback-reasons">${f.reasons.map((r) => `<span>${esc(r)}</span>`).join("")}</div>` : ""}
      `;
      box.appendChild(d);
    });
  if (!state.feedbacks.length) box.innerHTML = `<div style="padding:24px;text-align:center;color:var(--ink-3);font-size:12px;">还没有任何反馈。试试点 AI 回复下面的 👍 或 👎</div>`;
}
function bindFeedback() {
  $$(".f-tab").forEach((t) => {
    t.onclick = () => {
      state.feedbackFilter = t.dataset.fTab;
      $$(".f-tab").forEach((x) => x.classList.toggle("active", x === t));
      renderFeedbackList();
    };
  });
  $("#btn-feedback-clear-down")?.addEventListener("click", async () => {
    if (!confirm("清除所有点踩？")) return;
    try { await api("/api/feedbacks?vote=down", { method: "DELETE" }); toast("已清除"); await loadFeedbacks(); }
    catch (e) { toast(e.message, true); }
  });
}

/* Modal & Toast */
function openModal(title, html) {
  $("#modal-title").textContent = title; $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function bindModal() {
  $("#modal-close")?.addEventListener("click", () => $("#modal").classList.add("hidden"));
  $("#modal")?.addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });
}
let toastTimer;
function toast(msg, isErr) {
  const t = $("#toast"); if (!t) return;
  t.textContent = msg; t.style.color = isErr ? "#dc2626" : "";
  t.classList.remove("hidden"); t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

function fmtDate(s) {
  if (!s) return "";
  try {
    const d = new Date(s);
    const today = new Date(); const sameDay = d.toDateString() === today.toDateString();
    if (sameDay) return `今天 ${LZ(d.getHours())}:${LZ(d.getMinutes())}`;
    return `${d.getMonth() + 1}/${d.getDate()} ${LZ(d.getHours())}:${LZ(d.getMinutes())}`;
  } catch { return s; }
}

// 启动
try {
  boot();
} catch (bootErr) {
  console.error("BOOT FATAL:", bootErr);
}
