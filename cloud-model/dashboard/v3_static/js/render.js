import {state} from "./state.js";

let currentCameraFrameUrl = "";

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

export function applyTheme(theme) {
  const next = theme === "night" ? "night" : "day";
  document.body.dataset.theme = next;
  const button = document.getElementById("themeToggleButton");
  if (button) button.textContent = next === "night" ? "白天" : "夜间";
}

export function renderConfigForms() {
  const config = state.config;
  const settingsForm = document.getElementById("settingsForm");
  if (settingsForm) {
    settingsForm.cameraRefreshMs.value = config.cameraRefreshMs;
    settingsForm.statusRefreshMs.value = config.statusRefreshMs;
    settingsForm.systemRefreshMs.value = config.systemRefreshMs;
    settingsForm.historyPageSize.value = config.historyPageSize;
    settingsForm.theme.value = config.theme;
  }
  const sleepForm = document.getElementById("sleepSettingsForm");
  if (sleepForm) {
    sleepForm.sleepTime.value = config.sleepTime;
    sleepForm.sleepReminderText.value = config.sleepReminderText;
    const children = new Set(config.sleepChildren || []);
    sleepForm.querySelectorAll('input[name="sleepChildren"]').forEach(input => {
      input.checked = children.has(input.value);
    });
    renderSleepRecipients();
  }
}

export function renderSleepRecipients() {
  const list = document.getElementById("sleepRecipientList");
  const summary = document.getElementById("sleepRecipientSummary");
  if (!list || !summary) return;
  const selected = [];
  list.querySelectorAll(".recipient-card").forEach(card => {
    const input = card.querySelector('input[name="sleepChildren"]');
    const badge = card.querySelector(".recipient-badge");
    const checked = Boolean(input?.checked);
    card.classList.toggle("selected", checked);
    if (badge) badge.textContent = checked ? "提醒" : "不提醒";
    if (checked && input) selected.push(input.value);
  });
  summary.textContent = selected.length ? `将提醒：${selected.join("、")}` : "未选择提醒对象";
}

export function renderStatus(payload) {
  state.status = payload;
  setText("cameraSource", payload.camera?.label || "--");
  setText("cameraFps", `${payload.camera?.fpsTarget || "--"} FPS 目标`);
  setText("liveUpdated", `最近更新：${formatClock(payload.timestamp)}`);
  renderHomeMetrics(payload);
  renderCareSummary(payload);
  renderCareEvents(payload);
}

export function renderCameraFrameBlob(blob) {
  if (!blob || !blob.size) return;
  const nextUrl = URL.createObjectURL(blob);
  const previousUrl = currentCameraFrameUrl;
  currentCameraFrameUrl = nextUrl;
  for (const id of ["liveFrame", "controlFrame"]) {
    const img = document.getElementById(id);
    if (img) img.src = nextUrl;
  }
  if (previousUrl) {
    setTimeout(() => URL.revokeObjectURL(previousUrl), 5000);
  }
  setText("liveUpdated", `最近更新：${formatClock(new Date().toISOString())}`);
}

function renderHomeMetrics(payload) {
  const box = document.getElementById("homeMetrics");
  if (!box) return;
  const items = [
    ["温度", `${payload.environment?.temperatureC ?? "--"}°C`, "舒适范围"],
    ["湿度", `${payload.environment?.humidity ?? "--"}%`, "正常"],
    ["孩子状态", payload.child?.state || "--", (payload.child?.visibleChildren || []).join("、") || "等待识别"],
    ["看护状态", payload.safety?.state || "--", payload.safety?.summary || "--"],
  ];
  box.innerHTML = items.map(([label, value, note]) => `
    <div class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
      <div class="metric-note">${escapeHtml(note)}</div>
    </div>
  `).join("");
}

function renderCareSummary(payload) {
  const box = document.getElementById("careSummary");
  if (!box) return;
  const sleep = payload.sleep || {};
  const items = [
    ["当前看护", payload.safety?.state || "--", payload.safety?.summary || "--"],
    ["睡眠提醒", sleep.time || "--", `孩子名单：${(sleep.children || []).join("、") || "--"}`],
    ["助眠状态", sleep.aid?.active ? "进行中" : "未开始", `${sleep.aid?.mode || "白噪声"} · ${sleep.aid?.active ? "倒计时" : "待命"}`],
  ];
  box.innerHTML = items.map(([label, value, note]) => `
    <div class="summary-card">
      <div class="summary-label">${escapeHtml(label)}</div>
      <div class="summary-value">${escapeHtml(value)}</div>
      <div class="summary-note">${escapeHtml(note)}</div>
    </div>
  `).join("");
}

function renderCareEvents(payload) {
  const box = document.getElementById("careEvents");
  if (!box) return;
  const visible = (payload.child?.visibleChildren || []).join("、") || "暂无";
  const events = [
    ["现在", "睡眠 presence", `画面内孩子：${visible}`, "blue"],
    ["刚刚", "安全守护正常", payload.safety?.summary || "等待状态", "ok"],
    ["19:31", "睡眠提醒已配置", payload.sleep?.reminderText || "--", "warn"],
  ];
  box.innerHTML = events.map(([time, title, note, tag]) => `
    <div class="event-row">
      <div class="event-time">${escapeHtml(time)}</div>
      <div><div class="event-title">${escapeHtml(title)}</div><div class="event-note">${escapeHtml(note)}</div></div>
      <span class="tag ${escapeHtml(tag)}">${escapeHtml(tagLabel(tag))}</span>
    </div>
  `).join("");
}

export function renderConversation() {
  const box = document.getElementById("conversationList");
  if (!box) return;
  const messages = [
    ["child", "孩子 · 19:42", "小智，给我讲一个故事吧。"],
    ["robot", "小智 · 19:42", "我在。给你讲一个短一点的睡前故事。"],
    ["parent", "家长 · 19:45", state.config.sleepReminderText],
  ];
  box.innerHTML = messages.map(([type, meta, text]) => `
    <div class="message ${escapeHtml(type)}">
      <div class="message-meta">${escapeHtml(meta)}</div>
      <div class="message-bubble">${escapeHtml(text)}</div>
    </div>
  `).join("");
  box.scrollTop = box.scrollHeight;
}

export function renderHistory(payload) {
  state.history = payload;
  const box = document.getElementById("historyCategories");
  if (!box) return;
  const limit = Number(state.config.historyPageSize || 6);
  const rows = payload.dateRows || [];
  if (rows.length) {
    box.innerHTML = rows.map(row => {
      const items = row.items || [];
      const visibleCount = Math.max(1, limit - 1);
      const visible = items.slice(0, visibleCount);
      const extra = Math.max(0, items.length - visible.length);
      return `
        <div class="history-date-row">
          <div class="date-row-title">
            <div>
              <strong>${escapeHtml(formatDateLabel(row.date))}</strong>
              <span>${escapeHtml(row.date)} · ${items.length} 张</span>
            </div>
          </div>
          <div class="album-row" aria-label="${escapeHtml(row.date)} 历史图片">
            ${visible.map(item => historyCard(item)).join("")}
            ${extra ? `<button class="history-card more-card" data-more="${escapeHtml(row.date)}" data-more-count="${extra}"><strong>更多图片</strong><span>还有 ${extra} 张</span></button>` : ""}
          </div>
        </div>
      `;
    }).join("");
    return;
  }
  box.innerHTML = (payload.categories || []).map(category => {
    const items = category.items || [];
    const visible = items.slice(0, limit);
    const extra = Math.max(0, items.length - visible.length);
    return `
      <div class="history-category">
        <div class="category-title">
          <span>${escapeHtml(category.title)}</span>
          <span>${items.length} 张</span>
        </div>
        <div class="history-row">
          ${visible.map(item => historyCard(item)).join("")}
          ${extra ? `<button class="history-card more-card" data-more="${escapeHtml(category.id)}">查看更多 ${extra}</button>` : ""}
        </div>
      </div>
    `;
  }).join("");
}

export function renderHistoryGallery(payload) {
  const box = document.getElementById("historyGalleryContent");
  if (!box) return;
  const rows = payload.dateRows || [];
  setText(
    "historyGallerySummary",
    `${payload.dateFrom || "--"} 至 ${payload.dateTo || "--"} · ${galleryCategoryLabel(payload)} · ${rows.reduce((sum, row) => sum + (row.items || []).length, 0)} 张`
  );
  if (!rows.length) {
    box.innerHTML = `<div class="empty-state">没有找到符合条件的历史画面</div>`;
    return;
  }
  box.innerHTML = rows.map(row => `
    <div class="history-date-row gallery-date-row">
      <div class="date-row-title">
        <div>
          <strong>${escapeHtml(formatDateLabel(row.date))}</strong>
          <span>${escapeHtml(row.date)} · ${(row.items || []).length} 张</span>
        </div>
      </div>
      <div class="album-row gallery-album-row">
        ${(row.items || []).map(item => historyCard(item)).join("")}
      </div>
    </div>
  `).join("");
}

function historyCard(item) {
  const encoded = encodeURIComponent(JSON.stringify(item));
  const subtitle = [item.time, item.categoryTitle || item.source].filter(Boolean).join(" · ");
  return `
    <button class="history-card" data-preview="${encoded}" aria-label="${escapeHtml(item.event)} ${escapeHtml(item.time)}">
      <img src="${escapeHtml(item.imageUrl)}" alt="${escapeHtml(item.event)}" loading="lazy" />
      <div class="image-meta">
        <strong>${escapeHtml(item.event)}</strong>
        <span>${escapeHtml(subtitle)}</span>
      </div>
    </button>
  `;
}

export function openPreview(item) {
  const modal = document.getElementById("imagePreviewModal");
  const image = document.getElementById("previewImage");
  const caption = document.getElementById("previewCaption");
  image.src = item.imageUrl;
  caption.innerHTML = `<strong>${escapeHtml(item.event)}</strong><span>${escapeHtml(item.time)} · ${escapeHtml(item.source)}</span>`;
  modal.classList.add("open");
}

export function renderSystem(payload) {
  state.system = payload;
  setText("systemUpdated", `最近刷新：${formatClock(payload.updatedAt)} · ${payload.refreshMs} ms`);
  const box = document.getElementById("systemComponents");
  if (!box) return;
  box.innerHTML = (payload.components || []).map(item => `
    <div class="component-row">
      <div>
        <div class="component-name">${escapeHtml(item.name)}</div>
        <div class="component-detail">${escapeHtml(item.detail)} · ${escapeHtml(item.resource)}</div>
      </div>
      <span class="tag ${statusClass(item.status)}">${statusText(item.status)}</span>
    </div>
  `).join("");
}

export function showToast(text) {
  const toast = document.getElementById("toast");
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    toast.hidden = true;
  }, 1800);
}

function setText(id, text) {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
}

function formatClock(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(11, 19) || "--";
  return date.toLocaleTimeString("zh-CN", {hour12: false});
}

function formatDateLabel(value) {
  const today = new Date();
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "历史";
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const deltaDays = Math.round((date.getTime() - start.getTime()) / 86400000);
  if (deltaDays === 0) return "今天";
  if (deltaDays === -1) return "昨天";
  return date.toLocaleDateString("zh-CN", {month: "long", day: "numeric"});
}

function tagLabel(tag) {
  return {ok: "正常", warn: "提醒", blue: "信息", danger: "危险"}[tag] || "状态";
}

function galleryCategoryLabel(payload) {
  if (!payload?.category || payload.category === "all") return "全部类型";
  const selected = payload.selectedCategories || String(payload.category).split(",");
  const typeMap = new Map((payload.types || []).map(item => [item.id, item.title]));
  const names = selected.map(id => typeMap.get(id) || id).filter(Boolean);
  if (!names.length) return "全部类型";
  return names.length <= 2 ? names.join("、") : `已选 ${names.length} 类`;
}

function statusClass(status) {
  if (status === "running") return "ok";
  if (status === "standby") return "blue";
  if (status === "warning") return "warn";
  return "danger";
}

function statusText(status) {
  return {running: "运行", standby: "待命", warning: "告警"}[status] || "异常";
}
