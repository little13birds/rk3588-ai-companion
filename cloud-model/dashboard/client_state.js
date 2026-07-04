(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.DashboardClientState = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : window, function () {
  const REFRESH_POLICY = Object.freeze({
    cameraFrameMs: 67,
    liveStatusMs: 15000,
    liveEnvMs: 10000,
    liveAlertsMs: 15000,
    careCameraMs: 67,
    careSafetyMs: 3000,
    careAlertsMs: 3000,
    careSleepMs: 3000,
    controlCameraMs: 67,
    controlStatusMs: 3000,
  });

  function pad2(n) {
    return String(n).padStart(2, '0');
  }

  function parseDateKey(dateKey) {
    const text = String(dateKey || '').trim();
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }

  function formatLocalDateKey(value = new Date()) {
    return [
      value.getFullYear(),
      pad2(value.getMonth() + 1),
      pad2(value.getDate()),
    ].join('-');
  }

  function historyDateLabel(dateKey, todayKey = formatLocalDateKey()) {
    const current = String(dateKey || '').slice(0, 10);
    const today = String(todayKey || '').slice(0, 10);
    if (current === today) return '今天';

    const todayDate = parseDateKey(today);
    const date = parseDateKey(current);
    if (!todayDate || !date) return current || today;

    const yesterday = new Date(todayDate.getFullYear(), todayDate.getMonth(), todayDate.getDate() - 1);
    if (date.getFullYear() === yesterday.getFullYear() &&
        date.getMonth() === yesterday.getMonth() &&
        date.getDate() === yesterday.getDate()) {
      return '昨天';
    }
    return current;
  }

  function shiftDateKey(dateKey, deltaDays, todayKey = formatLocalDateKey()) {
    const current = parseDateKey(dateKey) || parseDateKey(todayKey) || new Date();
    const next = new Date(current.getFullYear(), current.getMonth(), current.getDate() + Number(deltaDays || 0));
    const today = parseDateKey(todayKey) || new Date();
    today.setHours(0, 0, 0, 0);
    next.setHours(0, 0, 0, 0);
    if (next > today) {
      return formatLocalDateKey(today);
    }
    return formatLocalDateKey(next);
  }

  function createPersistentAlertTracker(storage, storageKey) {
    const key = String(storageKey || 'alerts_seen');
    const memory = new Set();
    let loaded = false;

    function load() {
      if (loaded) return;
      loaded = true;
      if (!storage || typeof storage.getItem !== 'function') return;
      try {
        const raw = storage.getItem(key);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          for (const item of parsed) {
            if (item) memory.add(String(item));
          }
        }
      } catch (_) {}
    }

    function save() {
      if (!storage || typeof storage.setItem !== 'function') return;
      try {
        storage.setItem(key, JSON.stringify(Array.from(memory)));
      } catch (_) {}
    }

    return {
      has(id) {
        load();
        return memory.has(String(id || ''));
      },
      mark(id) {
        load();
        const token = String(id || '').trim();
        if (!token) return false;
        if (memory.has(token)) return false;
        memory.add(token);
        save();
        return true;
      },
      clear() {
        load();
        memory.clear();
        save();
      },
    };
  }

  function shouldBootDashboard(readyState) {
    return String(readyState || '').toLowerCase() !== 'loading';
  }

  function personUniqueName(person) {
    if (!person) return '';
    return String(person.unique_name || person.person_id || person.display_name || '').trim();
  }

  function personDisplayName(person) {
    if (!person) return '';
    return String(person.display_name || person.unique_name || person.person_id || '').trim();
  }

  function buildFindTargetPeople(people, sleepChildren) {
    const source = Array.isArray(people) ? people : [];
    const children = Array.isArray(sleepChildren) ? sleepChildren.map(name => String(name || '').trim()).filter(Boolean) : [];
    if (!source.length && !children.length) {
      return [{unique_name: 'nearest', display_name: '最近的人'}];
    }

    const byName = new Map();
    const result = [];
    function addPerson(person) {
      const uniqueName = personUniqueName(person);
      if (!uniqueName || byName.has(uniqueName)) return;
      const item = {
        unique_name: uniqueName,
        display_name: personDisplayName(person) || uniqueName,
      };
      byName.set(uniqueName, item);
      result.push(item);
    }

    const peopleByName = new Map(source.map(person => [personUniqueName(person), person]));
    for (const childName of children) {
      addPerson(peopleByName.get(childName) || {unique_name: childName, display_name: childName});
    }
    for (const person of source) {
      addPerson(person);
    }
    return result.length ? result : [{unique_name: 'nearest', display_name: '最近的人'}];
  }

  function describeFindChildTaskStatus(data, state) {
    const status = data || {};
    const previous = state || {};
    if (status.active) {
      return {
        text: `正在寻找 ${status.target || ''} · 剩余 ${status.remaining_sec ?? '--'} 秒`,
        autoClose: false,
        hasObservedActive: true,
      };
    }
    if (status.stopped_reason === 'arrived' && previous.hasObservedActive) {
      return {
        text: '已找到目标',
        autoClose: true,
        hasObservedActive: false,
      };
    }
    const reason = status.stopped_reason ? ` · ${status.stopped_reason}` : '';
    return {
      text: `未运行${reason}`,
      autoClose: false,
      hasObservedActive: false,
    };
  }

  return {
    REFRESH_POLICY,
    buildFindTargetPeople,
    createPersistentAlertTracker,
    describeFindChildTaskStatus,
    formatLocalDateKey,
    historyDateLabel,
    shouldBootDashboard,
    shiftDateKey,
  };
});
