export const pageMeta = {
  home: ["首页概览", "实时画面、孩子状态与最近对话"],
  care: ["看护与睡眠", "危险事件、睡眠监测和助眠控制"],
  control: ["机器人控制", "实时画面、方向键、找孩子和急停"],
  records: ["历史记录", "按日期与类别查看图片、读书和事件记录"],
  system: ["系统信息", "组件状态、资源占用和刷新配置"],
};

export const state = {
  page: "home",
  config: {
    cameraRefreshMs: 1000,
    statusRefreshMs: 2000,
    systemRefreshMs: 5000,
    historyPageSize: 6,
    sleepReminderText: "宝贝，该准备睡觉啦。",
    sleepTime: "21:30",
    sleepChildren: ["tao", "xiao"],
    theme: "day",
  },
  status: null,
  system: null,
  history: null,
  timers: new Map(),
};

export function todayISO() {
  const date = new Date();
  return dateToISO(date);
}

export function dateToISO(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function addDays(iso, delta) {
  const date = new Date(`${iso || todayISO()}T00:00:00`);
  if (Number.isNaN(date.getTime())) return todayISO();
  date.setDate(date.getDate() + delta);
  return dateToISO(date);
}

export function configureDateInput(input) {
  const today = new Date();
  const min = new Date(today);
  min.setDate(today.getDate() - 30);
  input.min = dateToISO(min);
  input.max = dateToISO(today);
  input.value = todayISO();
}

export function clampDateInput(input, value) {
  const candidate = value || input.value || todayISO();
  if (candidate < input.min) return input.min;
  if (candidate > input.max) return input.max;
  return candidate;
}
