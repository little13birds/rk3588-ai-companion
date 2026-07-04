import {api} from "./api.js";
import {
  addDays,
  clampDateInput,
  configureDateInput,
  pageMeta,
  state,
  todayISO,
} from "./state.js";
import {
  applyTheme,
  openPreview,
  renderConfigForms,
  renderConversation,
  renderCameraFrameBlob,
  renderHistory,
  renderHistoryGallery,
  renderSleepRecipients,
  renderStatus,
  renderSystem,
  showToast,
} from "./render.js";


document.addEventListener("DOMContentLoaded", init);
const PAGE_STORAGE_KEY = "xz.dashboard.activePage";
const MOVE_REPEAT_MS = 500;
let moveRepeatTimer = null;
let activeMoveDirection = "stop";
let moveInFlight = false;
let pendingMoveDirection = "";
let lastQueuedMoveDirection = "stop";
let cameraFrameInFlight = false;

async function init() {
  bindNavigation();
  bindGlobalActions();
  bindForms();
  bindHistory();
  bindDriveControls();
  bindModals();
  configureDateInput(document.getElementById("historyDate"));
  configureDateInput(document.getElementById("historyRangeStart"));
  configureDateInput(document.getElementById("historyRangeEnd"));
  switchPage(initialPage(), {persist: false, updateHash: false});
  await loadConfig();
  await refreshAll();
  await refreshCameraFrame({silent: true});
  startPolling();
}

function bindNavigation() {
  document.querySelectorAll(".side-nav-item").forEach(button => {
    button.addEventListener("click", () => switchPage(button.dataset.page));
  });
  window.addEventListener("hashchange", () => switchPage(pageFromHash(), {persist: true, updateHash: false}));
  document.getElementById("menuButton").addEventListener("click", openDrawer);
  document.getElementById("drawerBackdrop").addEventListener("click", closeDrawer);
}

function switchPage(page, options = {}) {
  if (!pageMeta[page]) return;
  const {persist = true, updateHash = true} = options;
  state.page = page;
  document.querySelectorAll(".page").forEach(node => node.classList.remove("active"));
  document.getElementById(`page-${page}`).classList.add("active");
  document.querySelectorAll(".side-nav-item").forEach(button => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  document.getElementById("pageTitle").textContent = pageMeta[page][0];
  document.getElementById("pageSubtitle").textContent = pageMeta[page][1];
  if (persist) safeLocalStorageSet(PAGE_STORAGE_KEY, page);
  if (updateHash && location.hash !== `#${page}`) {
    history.replaceState(null, "", `#${page}`);
  }
  closeDrawer();
}

function openDrawer() {
  document.getElementById("sidebar").classList.add("open");
  document.getElementById("drawerBackdrop").classList.add("open");
}

function closeDrawer() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("drawerBackdrop").classList.remove("open");
}

function bindGlobalActions() {
  document.getElementById("themeToggleButton").addEventListener("click", async () => {
    const next = state.config.theme === "night" ? "day" : "night";
    const ok = await guarded("切换主题失败", () => saveConfig({...state.config, theme: next}));
    if (ok) showToast(next === "night" ? "已切换到夜间模式" : "已切换到白天模式");
  });
  document.getElementById("refreshAllButton").addEventListener("click", async () => {
    const ok = await refreshAll();
    if (ok) showToast("页面数据已刷新");
  });
  document.getElementById("emergencyStopButton").addEventListener("click", async () => {
    const ok = await guarded("急停命令发送失败", () => api.emergencyStop());
    if (ok) showToast("已发送急停命令");
  });
  document.getElementById("sendSleepReminder").addEventListener("click", async () => {
    const result = await guarded("睡眠提醒发送失败", () => api.sleepReminder(state.config.sleepReminderText));
    if (result) showToast(`已发送提醒：${result.spokenText}`);
  });
  document.getElementById("openSleepSettings").addEventListener("click", () => {
    document.getElementById("sleepSettingsModal").classList.add("open");
  });
  document.getElementById("startAidButton").addEventListener("click", () => {
    document.getElementById("aidSettingsModal").classList.add("open");
  });
  document.getElementById("startAidConfirmButton").addEventListener("click", () => {
    const mode = document.getElementById("aidMode").value;
    const duration = document.getElementById("aidDuration").value;
    guarded("助眠启动失败", async () => {
      const result = await api.startSleepAid({type: mode, duration_min: Number(duration) || 20});
      document.getElementById("aidStatus").textContent = `${mode} · ${duration} · 进行中`;
      document.getElementById("aidSettingsModal").classList.remove("open");
      showToast(result?.aid_active === false ? "助眠状态已更新" : "助眠已开始");
      await refreshStatus({silent: true});
      return result;
    });
  });
  document.getElementById("stopAidButton").addEventListener("click", async () => {
    const ok = await guarded("助眠停止失败", () => api.stopSleepAid());
    if (ok) {
      document.getElementById("aidStatus").textContent = "未开始";
      showToast("助眠已停止");
      await refreshStatus({silent: true});
    }
  });
  document.getElementById("startFindButton").addEventListener("click", async () => {
    const target = document.getElementById("findTarget").value || "nearest";
    const ok = await guarded("开始寻找失败", () => api.findChild(target));
    if (ok) showToast(`开始寻找：${target}`);
  });
  document.getElementById("stopFindButton").addEventListener("click", async () => {
    const ok = await guarded("停止找人/跟随失败", () => api.stopPersonTask());
    if (ok) showToast("已停止找人/跟随任务");
  });
  document.getElementById("messageForm").addEventListener("submit", async event => {
    event.preventDefault();
    const input = document.getElementById("messageInput");
    const text = input.value.trim();
    if (!text) return;
    const ok = await guarded("家长播报发送失败", () => api.sendMessage(text));
    if (ok) {
      input.value = "";
      showToast("已加入播报队列：" + text);
    }
  });
}

function bindForms() {
  document.getElementById("settingsForm").addEventListener("submit", async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    const ok = await guarded("刷新设置保存失败", () => saveConfig({...state.config, ...data}));
    if (ok) showToast("刷新设置已保存");
  });
  document.getElementById("sleepSettingsForm").addEventListener("change", event => {
    if (event.target.matches('input[name="sleepChildren"]')) renderSleepRecipients();
  });
  document.getElementById("sleepSettingsForm").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    data.sleepChildren = Array.from(form.querySelectorAll('input[name="sleepChildren"]:checked')).map(input => input.value);
    if (!data.sleepChildren.length) {
      showToast("请至少选择一个睡眠提醒对象");
      return;
    }
    const ok = await guarded("睡眠设置保存失败", () => saveConfig({...state.config, ...data}));
    if (!ok) return;
    document.getElementById("sleepSettingsModal").classList.remove("open");
    showToast("睡眠设置已保存");
  });
}

function bindHistory() {
  const input = document.getElementById("historyDate");
  input.addEventListener("change", () => setHistoryDate(input.value));
  document.getElementById("prevDateButton").addEventListener("click", () => {
    setHistoryDate(addDays(input.value, -1));
  });
  document.getElementById("todayButton").addEventListener("click", () => {
    setHistoryDate(todayISO());
  });
  document.getElementById("nextDateButton").addEventListener("click", () => {
    setHistoryDate(addDays(input.value, 1));
  });
  document.getElementById("openHistoryGalleryButton").addEventListener("click", () => {
    openHistoryGalleryForDate(input.value);
  });
  document.getElementById("historyGalleryApply").addEventListener("click", loadHistoryGallery);
  document.getElementById("historyFilterToggle").addEventListener("click", event => {
    event.stopPropagation();
    toggleHistoryFilterDropdown();
  });
  document.getElementById("historyFilterTypes").addEventListener("change", updateHistoryFilterSummary);
  document.addEventListener("click", event => {
    if (!event.target.closest("#historyFilterDropdown")) closeHistoryFilterDropdown();
  });
  document.getElementById("historyCategories").addEventListener("click", event => {
    const card = event.target.closest("[data-preview]");
    if (card) {
      openPreview(JSON.parse(decodeURIComponent(card.dataset.preview)));
      return;
    }
    const more = event.target.closest("[data-more]");
    if (more) {
      openHistoryGalleryForDate(more.dataset.more);
    }
  });
  document.getElementById("historyGalleryContent").addEventListener("click", event => {
    const card = event.target.closest("[data-preview]");
    if (card) openPreview(JSON.parse(decodeURIComponent(card.dataset.preview)));
  });
}

function bindDriveControls() {
  const labels = {forward: "前进", backward: "后退", left: "左转", right: "右转", stop: "停止"};
  document.querySelectorAll("[data-drive]").forEach(button => {
    button.addEventListener("pointerdown", event => {
      event.preventDefault();
      button.setPointerCapture?.(event.pointerId);
      const action = button.dataset.drive;
      startDrive(action, labels[action] || action);
    });
    for (const eventName of ["pointerup", "pointerleave", "pointercancel", "lostpointercapture"]) {
      button.addEventListener(eventName, () => stopDrive());
    }
  });
  window.addEventListener("blur", stopDrive);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopDrive();
  });
}

function startDrive(direction, label) {
  clearMoveRepeat();
  activeMoveDirection = direction || "stop";
  void queueMoveCommand(activeMoveDirection, {silent: activeMoveDirection !== "stop"});
  document.getElementById("driveStatus").textContent =
    activeMoveDirection === "stop" ? "已停止" : `正在${label}`;
  if (activeMoveDirection === "stop") return;
  moveRepeatTimer = setInterval(() => {
    void queueMoveCommand(activeMoveDirection, {silent: true});
  }, MOVE_REPEAT_MS);
}

function stopDrive() {
  if (activeMoveDirection === "stop" && !moveRepeatTimer) return;
  clearMoveRepeat();
  activeMoveDirection = "stop";
  void queueMoveCommand("stop", {silent: true});
  document.getElementById("driveStatus").textContent = "已停止";
}

function clearMoveRepeat() {
  if (!moveRepeatTimer) return;
  clearInterval(moveRepeatTimer);
  moveRepeatTimer = null;
}

async function queueMoveCommand(direction, options = {}) {
  const nextDirection = direction || "stop";
  if (moveInFlight) {
    pendingMoveDirection = nextDirection;
    return null;
  }
  moveInFlight = true;
  lastQueuedMoveDirection = nextDirection;
  try {
    return await guarded("底盘控制失败", () => api.move(nextDirection), options);
  } finally {
    moveInFlight = false;
    const pending = pendingMoveDirection;
    pendingMoveDirection = "";
    if (pending) {
      void queueMoveCommand(pending, {silent: true});
    }
  }
}

function bindModals() {
  document.querySelectorAll("[data-close-modal]").forEach(button => {
    button.addEventListener("click", () => {
      button.closest(".modal-backdrop").classList.remove("open");
    });
  });
  document.querySelectorAll(".modal-backdrop").forEach(backdrop => {
    backdrop.addEventListener("click", event => {
      if (event.target === backdrop) backdrop.classList.remove("open");
    });
  });
}

async function loadConfig() {
  const payload = await guarded("配置加载失败", () => api.config());
  if (!payload) return;
  state.config = {...state.config, ...(payload.config || {})};
  applyConfig();
}

async function saveConfig(config) {
  const payload = await api.saveConfig(config);
  state.config = {...state.config, ...(payload.config || {})};
  applyConfig();
  startPolling();
}

function applyConfig() {
  applyTheme(state.config.theme);
  renderConfigForms();
  renderConversation();
}

async function refreshAll() {
  const results = await Promise.all([refreshStatus(), refreshSystem(), refreshHistory()]);
  return results.every(Boolean);
}

async function refreshStatus(options = {}) {
  const payload = await guarded("状态刷新失败", () => api.status(), options);
  if (!payload) return false;
  renderStatus(payload);
  return true;
}

async function refreshCameraFrame(options = {}) {
  if (cameraFrameInFlight || activeMoveDirection !== "stop") return false;
  cameraFrameInFlight = true;
  try {
    const blob = await guarded("画面刷新失败", () => api.snapshot(), options);
    if (!blob) return false;
    renderCameraFrameBlob(blob);
    return true;
  } finally {
    cameraFrameInFlight = false;
  }
}

async function refreshSystem(options = {}) {
  const payload = await guarded("系统信息刷新失败", () => api.system(), options);
  if (!payload) return false;
  renderSystem(payload);
  return true;
}

async function refreshHistory(options = {}) {
  const input = document.getElementById("historyDate");
  const payload = await guarded("历史记录刷新失败", () => api.history(input.value), options);
  if (!payload) return false;
  renderHistory(payload);
  input.value = payload.date;
  return true;
}

async function setHistoryDate(value) {
  const input = document.getElementById("historyDate");
  input.value = clampDateInput(input, value);
  await refreshHistory();
}

async function openHistoryGalleryForDate(value) {
  const start = document.getElementById("historyRangeStart");
  const end = document.getElementById("historyRangeEnd");
  const date = clampDateInput(document.getElementById("historyDate"), value || todayISO());
  start.value = date;
  end.value = date;
  closeHistoryFilterDropdown();
  updateHistoryFilterSummary();
  document.getElementById("historyGalleryModal").classList.add("open");
  await loadHistoryGallery();
}

async function loadHistoryGallery() {
  const start = document.getElementById("historyRangeStart");
  const end = document.getElementById("historyRangeEnd");
  start.value = clampDateInput(start, start.value);
  end.value = clampDateInput(end, end.value);
  const categories = selectedHistoryCategories();
  if (!categories.length) {
    showToast("请至少选择一种内容类型");
    return false;
  }
  closeHistoryFilterDropdown();
  const payload = await guarded("历史全部预览加载失败", () => api.historyGallery({
    category: categories.join(","),
    from: start.value,
    to: end.value,
  }));
  if (!payload) return false;
  renderHistoryGallery(payload);
  return true;
}

function selectedHistoryCategories() {
  return Array.from(document.querySelectorAll('input[name="historyFilterTypes"]:checked')).map(input => input.value);
}

function updateHistoryFilterSummary() {
  const allInputs = Array.from(document.querySelectorAll('input[name="historyFilterTypes"]'));
  const checked = allInputs.filter(input => input.checked);
  const summary = document.getElementById("historyFilterSummary");
  if (!summary) return;
  if (!checked.length) {
    summary.textContent = "未选择类型";
    return;
  }
  if (checked.length === allInputs.length) {
    summary.textContent = "全部类型";
    return;
  }
  const names = checked.map(input => input.closest("label")?.textContent?.trim() || input.value);
  summary.textContent = names.length <= 2 ? names.join("、") : `已选 ${names.length} 类`;
}

function toggleHistoryFilterDropdown() {
  const dropdown = document.getElementById("historyFilterDropdown");
  const toggle = document.getElementById("historyFilterToggle");
  const open = !dropdown.classList.contains("open");
  dropdown.classList.toggle("open", open);
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

function closeHistoryFilterDropdown() {
  const dropdown = document.getElementById("historyFilterDropdown");
  const toggle = document.getElementById("historyFilterToggle");
  if (!dropdown || !toggle) return;
  dropdown.classList.remove("open");
  toggle.setAttribute("aria-expanded", "false");
}

function startPolling() {
  clearPolling();
  state.timers.set("status", setInterval(() => refreshStatus({silent: true}), Number(state.config.statusRefreshMs)));
  state.timers.set("camera", setInterval(() => refreshCameraFrame({silent: true}), Number(state.config.cameraRefreshMs)));
  state.timers.set("system", setInterval(() => refreshSystem({silent: true}), Number(state.config.systemRefreshMs)));
}

function clearPolling() {
  state.timers.forEach(timer => clearInterval(timer));
  state.timers.clear();
}

async function guarded(message, action, options = {}) {
  try {
    return await action();
  } catch (error) {
    console.error(error);
    if (!options.silent) showToast(message);
    return null;
  }
}

function initialPage() {
  return pageFromHash() || safeLocalStorageGet(PAGE_STORAGE_KEY) || "home";
}

function pageFromHash() {
  const page = location.hash.replace(/^#/, "");
  return pageMeta[page] ? page : "";
}

function safeLocalStorageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (_error) {
    return "";
  }
}

function safeLocalStorageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_error) {
    // Ignore private-mode storage failures; URL hash still preserves the page.
  }
}
