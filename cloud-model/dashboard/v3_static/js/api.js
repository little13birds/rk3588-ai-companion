export async function getJson(path) {
  const response = await fetch(path, {cache: "no-store"});
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
  }
  return response.json();
}

export async function getBlob(path) {
  const response = await fetch(path, {cache: "no-store"});
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
  }
  return response.blob();
}

export async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload || {}),
  });
  if (!response.ok) {
    throw new Error(`POST ${path} failed: ${response.status}`);
  }
  return response.json();
}

export const api = {
  config: () => getJson("/api/config"),
  saveConfig: payload => postJson("/api/config", payload),
  status: () => getJson("/api/status"),
  snapshot: () => getBlob("/api/camera/snapshot"),
  system: () => getJson("/api/system/components"),
  history: date => getJson(`/api/history?date=${encodeURIComponent(date || "")}`),
  historyGallery: params => {
    const query = new URLSearchParams(params || {});
    return getJson(`/api/history/gallery?${query.toString()}`);
  },
  sleepReminder: text => postJson("/api/sleep/remind", {text}),
  sendMessage: text => postJson("/api/message/send", {text}),
  move: direction => postJson("/api/move", {direction}),
  findChild: target => postJson("/api/person-task/seek", {target, timeout_sec: 60}),
  startSleepAid: payload => postJson("/api/sleep/aid/start", payload),
  stopSleepAid: () => postJson("/api/sleep/aid/stop", {}),
  emergencyStop: () => postJson("/api/move/emergency-stop", {}),
  stopPersonTask: () => postJson("/api/person-task/stop", {}),
};
