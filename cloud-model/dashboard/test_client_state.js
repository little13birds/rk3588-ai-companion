const assert = require('node:assert/strict');

const client = require('./client_state.js');

function testHistoryDateHelpers() {
  assert.equal(client.formatLocalDateKey(new Date(2026, 5, 22, 1, 2, 3)), '2026-06-22');
  assert.equal(client.historyDateLabel('2026-06-22', '2026-06-22'), '今天');
  assert.equal(client.historyDateLabel('2026-06-21', '2026-06-22'), '昨天');
  assert.equal(client.historyDateLabel('2026-06-20', '2026-06-22'), '2026-06-20');
  assert.equal(client.shiftDateKey('2026-06-21', 1, '2026-06-22'), '2026-06-22');
  assert.equal(client.shiftDateKey('2026-06-22', 1, '2026-06-22'), '2026-06-22');
  assert.equal(client.shiftDateKey('2026-06-22', -1, '2026-06-22'), '2026-06-21');
}

function testRefreshPolicy() {
  assert.equal(client.REFRESH_POLICY.careSafetyMs, 3000);
  assert.equal(client.REFRESH_POLICY.careAlertsMs, 3000);
  assert.equal(client.REFRESH_POLICY.cameraFrameMs, 67);
  assert.equal(client.REFRESH_POLICY.controlCameraMs, 67);
}

function testPersistentAlertTracker() {
  const storage = {
    data: {},
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(this.data, key) ? this.data[key] : null;
    },
    setItem(key, value) {
      this.data[key] = String(value);
    },
  };
  const tracker1 = client.createPersistentAlertTracker(storage, 'alerts');
  assert.equal(tracker1.has('e1'), false);
  assert.equal(tracker1.mark('e1'), true);
  assert.equal(tracker1.has('e1'), true);
  assert.equal(tracker1.mark('e1'), false);

  const tracker2 = client.createPersistentAlertTracker(storage, 'alerts');
  assert.equal(tracker2.has('e1'), true);
  assert.equal(tracker2.mark('e2'), true);
}

function testBootDecision() {
  assert.equal(client.shouldBootDashboard('loading'), false);
  assert.equal(client.shouldBootDashboard('interactive'), true);
  assert.equal(client.shouldBootDashboard('complete'), true);
}

function testFindTargetsIncludeAllPeopleWithChildrenFirst() {
  const people = [
    {unique_name: 'tao', display_name: 'Tao'},
    {unique_name: 'xiao', display_name: 'Xiao'},
    {unique_name: 'chen', display_name: 'Chen'},
  ];
  const targets = client.buildFindTargetPeople(people, ['xiao']);
  assert.deepEqual(targets.map(item => item.unique_name), ['xiao', 'tao', 'chen']);
}

function testFindTargetsFallbackToNearestWhenEmpty() {
  const targets = client.buildFindTargetPeople([], []);
  assert.deepEqual(targets, [{unique_name: 'nearest', display_name: '最近的人'}]);
}

function testOldArrivedStatusDoesNotAutoCloseBeforeActiveTaskObserved() {
  const view = client.describeFindChildTaskStatus(
    {active: false, stopped_reason: 'arrived', target: 'tao'},
    {hasObservedActive: false},
  );
  assert.equal(view.text, '未运行 · arrived');
  assert.equal(view.autoClose, false);
  assert.equal(view.hasObservedActive, false);
}

function testArrivedStatusAutoClosesOnlyAfterActiveTaskObserved() {
  const activeView = client.describeFindChildTaskStatus(
    {active: true, target: 'tao', remaining_sec: 59},
    {hasObservedActive: false},
  );
  assert.equal(activeView.text, '正在寻找 tao · 剩余 59 秒');
  assert.equal(activeView.autoClose, false);
  assert.equal(activeView.hasObservedActive, true);

  const arrivedView = client.describeFindChildTaskStatus(
    {active: false, stopped_reason: 'arrived', target: 'tao'},
    {hasObservedActive: activeView.hasObservedActive},
  );
  assert.equal(arrivedView.text, '已找到目标');
  assert.equal(arrivedView.autoClose, true);
  assert.equal(arrivedView.hasObservedActive, false);
}

testHistoryDateHelpers();
testRefreshPolicy();
testPersistentAlertTracker();
testBootDecision();
testFindTargetsIncludeAllPeopleWithChildrenFirst();
testFindTargetsFallbackToNearestWhenEmpty();
testOldArrivedStatusDoesNotAutoCloseBeforeActiveTaskObserved();
testArrivedStatusAutoClosesOnlyAfterActiveTaskObserved();

console.log('test_client_state PASS');
