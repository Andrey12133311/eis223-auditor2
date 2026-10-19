/* Executable embedded-JS regressions. No browser, package install or live API required.
 * Run: node tests/test_account_frontend_requests.cjs
 * The small DOM double exercises real page scripts and records mocked HTTP calls.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, 'scripts', file), 'utf8');
const embedded = (file, assignment) => {
  const source = read(file);
  const start = source.indexOf(assignment);
  assert.ok(start >= 0, assignment);
  return source.slice(start).split('<script>')[1].split('</script>')[0];
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.nodeType = 1; this.children = [];
    this.value = ''; this.textContent = ''; this.dataset = {}; this.style = {};
    this.checked = false; this.disabled = false; this.listeners = {}; this.attrs = {};
    this.classList = {add() {}, remove() {}, toggle() {}};
    this.elements = {namedItem: name => this.querySelector(name)};
    this.formValues = {}; this.resetCount = 0;
  }
  get options() { return this.children; }
  get selectedOptions() { return this.children.filter(x => x.value === this.value); }
  get childElementCount() { return this.children.length; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attrs[name] = value; }
  getAttribute(name) { return this.attrs[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelector(selector) {
    this.queries ||= {};
    return this.queries[selector] ||= new Element(selector.includes('button') ? 'button' : 'input');
  }
  querySelectorAll(selector) {
    return selector === 'input,select,textarea,button' ? Object.values(this.queries || {}) : [];
  }
  reset() { this.resetCount++; this.formValues = {}; }
  scrollIntoView() {}
}
function harness(source, exposed, responder) {
  const nodes = new Map(), calls = [], timers = new Map(), intervals = [];
  let nextTimer = 1;
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const document = {
    hidden: false, getElementById: get, createElement: tag => new Element(tag),
    querySelectorAll: () => [], querySelector: () => null,
  };
  const context = {
    document, console, URL, URLSearchParams, location: {search: '', replace() {}, assign() {}},
    confirm: () => true, prompt: () => '',
    addEventListener() {},
    setTimeout: callback => { const id = nextTimer++; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id), setInterval: callback => intervals.push(callback),
    FormData: class {
      constructor(form) { this.values = form.formValues; }
      get(key) { return this.values[key]; }
      getAll(key) { return [].concat(this.values[key] || []); }
      entries() { return Object.entries(this.values); }
    },
    fetch: async (url, options = {}) => {
      const call = {url, method: options.method || 'GET', options}; calls.push(call);
      const answer = await responder(call);
      const status = answer?.httpStatus || 200;
      return {ok: status < 400, status, json: async () => answer?.httpStatus ? answer.body : answer};
    },
  };
  context.window = context;
  source = source.replace('__ROLE__', '"admin"');
  const end = source.lastIndexOf('})();');
  assert.ok(end >= 0);
  source = source.slice(0, end) + `globalThis.testAPI={${exposed}};\n` + source.slice(end);
  vm.runInNewContext(source, context, {timeout: 1000});
  return {
    get, calls, intervals, context, api: context.testAPI,
    count: url => calls.filter(call => call.url === url).length,
    flushTimers: async () => {
      const pending = [...timers.values()]; timers.clear();
      for (const callback of pending) await callback();
      await flush();
    },
  };
}
const manualRows = [
  {reg_number: '32616412317', customer: 'Alpha', customer_inn: '1', job_status: 'done',
    violations: 2, violation_types: ['Сроки'], started_at: '2026-10-18T12:00:00Z'},
  {reg_number: '32616412318', customer: 'Beta', customer_inn: '2', job_status: 'queued',
    violations: 0, violation_types: [], started_at: '2026-10-17T12:00:00Z'},
];

async function testManualFiltersAndRefreshRace() {
  let held = null, responseRows = manualRows;
  const ui = harness(embedded('account_pages_v1.py', 'HOME ='), 'refresh', async call => {
    if (call.method !== 'GET') return {};
    if (held) return held.promise;
    return responseRows;
  });
  await flush();
  assert.equal(ui.count('/me/manual-checks'), 1);
  assert.equal(ui.get('manual-rows').children.length, 2);
  ui.get('manual-search').value = 'alpha';
  ui.get('manual-search').listeners.input();
  await ui.flushTimers();
  assert.equal(ui.get('manual-rows').children.length, 1);
  ui.get('manual-status').value = 'queued';
  ui.get('manual-status').listeners.change();
  await ui.flushTimers();
  assert.equal(ui.get('manual-rows').children.length, 0);
  ui.get('manual-filters-reset').onclick();
  assert.equal(ui.get('manual-rows').children.length, 2);
  assert.equal(ui.count('/me/manual-checks'), 1, 'all local filters and reset must avoid GETs');

  held = deferred();
  const pending = ui.api.refresh();
  const same = ui.api.refresh();
  assert.equal(pending, same, 'ordinary overlapping reads share one request');
  const afterMutation = ui.api.refresh(true);
  assert.equal(ui.count('/me/manual-checks'), 2, 'mutation refresh must wait for old GET');
  const release = held; held = null; responseRows = [manualRows[1]];
  release.resolve(manualRows);
  await afterMutation;
  assert.equal(ui.count('/me/manual-checks'), 3, 'mutation queues one fresh GET');
  assert.equal(ui.get('manual-rows').children.length, 1, 'post-mutation data wins');
}

async function testThematicFiltersAndDeniedPolling() {
  const batches = manualRows.map((row, i) => ({id: String(i), customers: [row.customer],
    status: i ? 'stopped' : 'active', created_at: row.started_at, violation_types: row.violation_types}));
  const ui = harness(embedded('account_pages_v1.py', 'THEMATIC ='), 'refresh,loadHistorical', async call =>
    call.url.endsWith('/unassigned') ? {httpStatus: 403, body: {}} : {batches});
  await flush();
  ui.get('thematic-customer').value = 'alpha';
  ui.get('thematic-customer').listeners.input();
  await ui.flushTimers();
  assert.equal(ui.get('real-thematic-list').children.length, 1);
  assert.match(ui.get('thematic-status').textContent, /1 из 2/);
  ui.get('thematic-reset').onclick();
  assert.equal(ui.count('/api/thematic/history'), 1, 'thematic filters use cached data');
  assert.equal(ui.get('real-thematic-list').children.length, 2);
  for (const poll of ui.intervals) poll();
  await flush();
  assert.equal(ui.count('/api/thematic/unassigned'), 1, '403 stops migration-list polling');
  assert.equal(ui.count('/api/thematic/history'), 2, 'owned history still refreshes');
}

function staffResponse(call) {
  const {url, method} = call;
  if (method !== 'GET') return {batch_id: 'batch-1'};
  if (url === '/me/profile') return {full_name: 'Employee', role: 'user'};
  if (url === '/me/center/dashboard') return {tasks: [], campaigns: [], as_of: '2026-10-18T12:00:00Z'};
  if (url.endsWith('/progress')) return {available: true, checks: [], note: ''};
  if (url.endsWith('/overview')) return {};
  if (url.endsWith('/violations')) return {count: 0, findings: []};
  if (url.endsWith('/templates')) return [{id: 'template-1', name: 'Template', categories: ['dates'], legal_forms: ['ООО']}];
  if (url.endsWith('/options')) return {categories: {dates: 'Сроки'}, legal_forms: {ООО: 'ООО'}};
  if (url.endsWith('/preferences')) return {priority_regs: []};
  if (url.endsWith('/related')) return {purchases: []};
  if (url.endsWith('/versions')) return {groups: [], note: ''};
  if (url.includes('/draft/')) return {body: url.slice(-11)};
  return [];
}

async function testStaffRequestsAndAsyncForms() {
  const ui = harness(embedded('v370_staff_ui.py', 'JS='),
    'section,templates,loadCase,loadDashboard,loadToday', staffResponse);
  await flush();
  assert.equal(ui.calls.length, 6, 'startup only loads profile and visible overview data');
  assert.equal(ui.count('/me/campaign-goals'), 0);
  assert.equal(ui.count('/me/workbench/history'), 0);
  await ui.api.section('templates');
  const card = ui.get('staff-template-list').children[0];
  const launch = card.children.at(-1).children[1];
  await launch.onclick();
  assert.equal(ui.count('/me/workbench/templates/template-1/start'), 1);
  assert.equal(ui.count('/me/campaign-goals'), 1, 'launch triggers one campaign read');
  assert.equal(ui.count('/me/campaign-goals/violations'), 1);
  await ui.api.section('templates');
  assert.equal(ui.count('/me/workbench/options'), 1, 'static form options preserve edits on reentry');

  const reg = '32616412317'; ui.get('staff-reg').value = reg;
  await ui.api.loadCase();
  for (const [id, values, resource] of [
    ['staff-manual-form', {title: 'Manual finding', evidence: 'Evidence'}, 'manual-findings'],
    ['staff-message-form', {body: 'A discussion message'}, 'discussion'],
  ]) {
    const form = ui.get(id); form.formValues = values;
    const beforeReset = form.resetCount;
    const event = {currentTarget: form, preventDefault() {}};
    const pending = form.onsubmit(event);
    event.currentTarget = null; // Native DOM clears this as soon as dispatch returns.
    await pending;
    assert.equal(form.resetCount, beforeReset + 1, `${id} resets after successful asynchronous POST`);
    assert.equal(form.querySelector('button').disabled, false);
    assert.equal(ui.calls.filter(call => call.url.endsWith('/'+resource) && call.method === 'POST').length, 1);
    assert.equal(ui.calls.filter(call => call.url.endsWith('/'+resource) && call.method === 'GET').length, 2);
  }
}

async function testParallelCaseReadsIgnoreOlderResponses() {
  const held = deferred(), oldReg = '32616412317', newReg = '32616412318';
  const ui = harness(embedded('v370_staff_ui.py', 'JS='), 'loadCase', async call => {
    if (call.url.includes(oldReg)) { await held.promise; }
    return staffResponse(call);
  });
  await flush();
  ui.get('staff-reg').value = oldReg;
  const first = ui.api.loadCase();
  assert.equal(ui.calls.filter(call => call.url.includes(oldReg)).length, 6,
    'all six case-specific reads start before any resolves, plus shared preferences');
  assert.equal(ui.count('/me/workbench/preferences'), 1);
  ui.get('staff-reg').value = newReg;
  await ui.api.loadCase();
  assert.equal(ui.get('staff-case-draft').value, newReg);
  held.resolve(); await first;
  assert.equal(ui.get('staff-case-draft').value, newReg, 'old case response cannot replace current draft');
  assert.equal(ui.get('staff-case-draft').dataset.reg, newReg);
}

async function testDashboardSharingAndMutationRefresh() {
  let held = null;
  const ui = harness(embedded('v370_staff_ui.py', 'JS='), 'loadDashboard,loadToday', async call => {
    if (held && call.url === '/me/center/dashboard') await held.promise;
    return staffResponse(call);
  });
  await flush(); held = deferred();
  const first = ui.api.loadDashboard(), second = ui.api.loadToday();
  const fresh = ui.api.loadDashboard(true);
  assert.equal(ui.count('/me/center/dashboard'), 2, 'concurrent views share one GET');
  held.resolve(); held = null;
  await Promise.all([first, second, fresh]);
  assert.equal(ui.count('/me/center/dashboard'), 3, 'mutation forces a new read after in-flight GET');
}

async function testManagerSearchAndReloadQueue() {
  let held = null;
  const ui = harness(embedded('v368_manager_ui.py', 'SCRIPT='), 'reload', async call => {
    if (held && call.url === '/manager/dashboard') await held.promise;
    if (call.url === '/me/profile') return {full_name: 'Admin'};
    if (call.url === '/manager/dashboard') return {tasks_by_day: []};
    if (call.url.endsWith('/mail/status')) return {configured: true};
    if (call.url.endsWith('/options')) return {categories: {}, legal_forms: {}};
    if (call.url.endsWith('/quality')) return {issues: []};
    return [];
  });
  await flush();
  assert.equal(ui.count('/manager/admin/mail/status'), 1);
  ui.get('mgr-admin-search').value = 'admin';
  for (let i = 0; i < 6; i++) ui.get('mgr-admin-search').listeners.input();
  ui.get('mgr-admin-status').listeners.change();
  await flush();
  assert.equal(ui.count('/manager/admin/mail/status'), 1, 'user filters never refetch mail status');
  held = deferred();
  const first = ui.api.reload();
  const second = ui.api.reload(), third = ui.api.reload();
  assert.equal(first, second); assert.equal(second, third);
  assert.equal(ui.count('/manager/dashboard'), 2, 'overlapping reloads do not fan out');
  assert.equal(ui.count('/manager/admin/users'), 2, 'admin list starts in the main parallel batch');
  held.resolve(); held = null;
  await first;
  assert.equal(ui.count('/manager/dashboard'), 3, 'queued refresh runs once after the first batch');
}

async function testHistoricalAdoptionQueuesFreshRead() {
  let held = null, rows = [{id: 'legacy-1', categories: [], created_at: '2026-10-18'}];
  const ui = harness(embedded('account_pages_v1.py', 'THEMATIC ='), 'loadHistorical', async call => {
    if (call.url.endsWith('/unassigned')) return held ? held.promise : {batches: rows};
    return {batches: []};
  });
  await flush();
  const button = ui.get('unassigned-list').children[0].children.at(-1);
  held = deferred();
  const oldRead = ui.api.loadHistorical();
  const adopted = button.onclick();
  await flush();
  assert.equal(ui.count('/api/thematic/unassigned'), 2, 'adoption waits for in-flight read');
  const release = held; held = null; rows = [];
  release.resolve({batches: [{id: 'legacy-1', categories: []}]});
  await Promise.all([oldRead, adopted]);
  assert.equal(ui.count('/api/thematic/unassigned'), 3, 'adoption queues fresh migration list');
  assert.equal(ui.get('unassigned-list').children.length, 0);
}

async function testCaseSwitchDisablesOldDraftAndControls() {
  const oldReg = '32616412317', nextReg = '32616412318';
  let held = null;
  const ui = harness(embedded('v370_staff_ui.py', 'JS='), 'loadCase', async call => {
    if (held && call.url.includes(nextReg)) await held.promise;
    if (call.url === '/me/center/draft/' + nextReg) return {httpStatus: 503, body: {}};
    return staffResponse(call);
  });
  await flush();
  const manualButton = ui.get('staff-manual-form').querySelector('button');
  ui.get('staff-reg').value = oldReg; await ui.api.loadCase();
  assert.equal(ui.get('staff-case-draft').dataset.reg, oldReg);
  assert.equal(manualButton.disabled, false);
  ui.get('staff-checklist').append(new Element('button'));
  held = deferred(); ui.get('staff-reg').value = nextReg;
  const pending = ui.api.loadCase();
  assert.equal(ui.get('staff-case-draft').value, '');
  assert.equal(ui.get('staff-case-draft').dataset.reg, '');
  assert.equal(ui.get('staff-case-draft').disabled, true);
  assert.equal(manualButton.disabled, true);
  assert.equal(ui.get('staff-checklist').children.length, 0);
  ui.get('staff-case-draft').listeners.input();
  await ui.flushTimers();
  assert.equal(ui.calls.filter(call => call.method === 'PUT').length, 0, 'unloaded draft cannot autosave');
  held.resolve(); held = null; await pending;
  assert.equal(ui.get('staff-case-draft').disabled, true, 'failed draft load remains disabled');
  assert.equal(ui.get('staff-case-draft').dataset.reg, '');
  assert.equal(manualButton.disabled, false, 'successfully loaded independent section stays usable');
  ui.get('staff-reg').value = oldReg; // Merely editing the selector does not change the open case.
  const form = ui.get('staff-message-form'); form.formValues = {body: 'For the loaded case'};
  await form.onsubmit({currentTarget: form, preventDefault() {}});
  assert.equal(ui.calls.filter(call => call.method === 'POST').at(-1).url,
    '/me/workbench/' + nextReg + '/discussion');
}

async function testDirtyDraftSurvivesRefreshAndCaseSwitch() {
  const firstReg = '32616412317', secondReg = '32616412318';
  const ui = harness(embedded('v370_staff_ui.py', 'JS='), 'loadCase', staffResponse);
  await flush();
  ui.get('staff-reg').value = firstReg; await ui.api.loadCase();
  const draft = ui.get('staff-case-draft');
  draft.value = 'Unsaved first case'; draft.listeners.input();
  await ui.api.loadCase();
  assert.equal(draft.value, 'Unsaved first case', 'same-case refresh preserves debounce-pending text');
  ui.get('staff-reg').value = secondReg; await ui.api.loadCase();
  draft.value = 'Unsaved second case'; draft.listeners.input();
  ui.get('staff-reg').value = firstReg; await ui.api.loadCase();
  assert.equal(draft.value, 'Unsaved first case', 'A to B to A restores pending local draft');
  await ui.flushTimers();
  const writes = ui.calls.filter(call => call.method === 'PUT');
  assert.equal(writes.length, 2, 'editing B does not cancel A autosave');
  assert.deepEqual(writes.map(call => [call.url, JSON.parse(call.options.body).body]), [
    ['/me/center/draft/' + firstReg, 'Unsaved first case'],
    ['/me/center/draft/' + secondReg, 'Unsaved second case'],
  ]);
}

async function testOldSubmissionCannotResetNewCaseForm() {
  for (const [id, resource] of [['staff-manual-form', 'manual-findings'], ['staff-message-form', 'discussion']]) {
    const oldReg = '32616412317', nextReg = '32616412318';
    const post = deferred(), nextReads = deferred();
    const ui = harness(embedded('v370_staff_ui.py', 'JS='), 'loadCase', async call => {
      if (call.method === 'POST') await post.promise;
      if (call.method === 'GET' && call.url.includes(nextReg)) await nextReads.promise;
      return staffResponse(call);
    });
    await flush();
    const form = ui.get(id), button = form.querySelector('button');
    ui.get('staff-reg').value = oldReg; await ui.api.loadCase();
    form.formValues = {title: 'Old case', body: 'Old discussion'};
    const submission = form.onsubmit({currentTarget: form, preventDefault() {}});
    ui.get('staff-reg').value = nextReg; const opening = ui.api.loadCase();
    const resets = form.resetCount;
    form.formValues = {title: 'New case text', body: 'New discussion text'};
    post.resolve(); await submission;
    assert.equal(form.resetCount, resets, `${resource}: older POST must not reset new form`);
    assert.equal(form.formValues.title, 'New case text');
    assert.equal(button.disabled, true, `${resource}: older POST must not unlock loading controls`);
    nextReads.resolve(); await opening;
    assert.equal(button.disabled, false);
  }
}

async function testAutosavesStayOrdered() {
  const held = deferred(), reg = '32616412317';
  let writes = 0;
  const ui = harness(embedded('v370_staff_ui.py', 'JS='), 'loadCase', async call => {
    if (call.method === 'PUT' && ++writes === 1) await held.promise;
    return staffResponse(call);
  });
  await flush(); ui.get('staff-reg').value = reg; await ui.api.loadCase();
  const draft = ui.get('staff-case-draft');
  draft.value = 'First version'; draft.listeners.input();
  const saving = ui.flushTimers();
  draft.value = 'Second version'; draft.listeners.input();
  await ui.flushTimers();
  assert.equal(writes, 1, 'second save cannot race the first write');
  await ui.api.loadCase();
  assert.equal(draft.value, 'Second version', 'pending write protects newer edit on refresh');
  held.resolve(); await saving;
  const bodies = ui.calls.filter(call => call.method === 'PUT').map(call => JSON.parse(call.options.body).body);
  assert.deepEqual(bodies, ['First version', 'Second version']);
}

async function testOlderCaseSearchesCannotRenderIntoNewCase() {
  const held = deferred(), oldReg = '32616412317', nextReg = '32616412318';
  const ui = harness(embedded('v370_staff_ui.py', 'JS='), 'loadCase', async call => {
    if (call.url.includes('?')) {
      await held.promise;
      return {results: [{type: 'document', excerpt: 'Old excerpt', name: 'Old document', fragment: 'Old text'}],
        diff: ['+Old version'], note: '', scope: ''};
    }
    return staffResponse(call);
  });
  await flush(); ui.get('staff-reg').value = oldReg; await ui.api.loadCase();
  const searches = [];
  for (const id of ['staff-search-form', 'staff-original-search']) {
    const form = ui.get(id); form.formValues = {q: 'search phrase'};
    searches.push(form.onsubmit({currentTarget: form, preventDefault() {}}));
  }
  ui.get('staff-version-left').value = '1'; ui.get('staff-version-right').value = '2';
  searches.push(ui.get('staff-version-compare').onclick());
  ui.get('staff-reg').value = nextReg; await ui.api.loadCase();
  held.resolve(); await Promise.all(searches);
  for (const id of ['staff-search-results', 'staff-original-results', 'staff-version-diff'])
    assert.equal(ui.get(id).children.length, 0, id + ' must not show older case response');
}

const flatten = node => [node, ...node.children.flatMap(flatten)];
const words = node => flatten(node).map(x => x.textContent).join(' ');

async function testLegalStatusSeparateFromCompletedDownload() {
  for (const [file, assignment, target] of [
    ['account_pages_v1.py', 'HOME =', 'manual-rows'],
    ['account_site_ui_v1.py', 'def site_header(', 'eis223-my-procurements'],
  ]) {
    let row = {...manualRows[0], job_status: 'completed', documents_checked: 2, card_available: true,
      legal_review_state: 'pending', legal_review_documents: 2, legal_review_completed: false};
    const ui = harness(embedded(file, assignment), 'refresh', async call => {
      if (call.url === '/me/manual-checks') return [row];
      if (call.url === '/me/profile') return {completed: true, full_name: 'Employee'};
      if (call.url === '/api/thematic/history') return {batches: []};
      return [];
    });
    await flush(); await ui.api.refresh();
    for (const [state, documents, expected] of [
      ['pending', 2, 'Юридическая проверка в очереди'],
      ['running', 2, 'Идёт юридическая проверка'],
      ['completed', 2, 'Юридическая проверка завершена'],
      ['completed', 0, 'Юридическая проверка не завершена: нет проверенных документов'],
      ['failed', 2, 'Ошибка юридической проверки'],
      ['no_readable_documents', 0, 'Не удалось прочитать документы для юридической проверки'],
      ['unknown', 0, 'Юридическая проверка ещё не завершена'],
      ['unexpected_state', 0, 'Юридическая проверка ещё не завершена'],
    ]) {
      row = {...row, legal_review_state: state, legal_review_documents: documents};
      await ui.api.refresh();
      const rendered = words(ui.get(target));
      assert.ok(rendered.includes('Загрузка завершена'), file + ': download label retained');
      assert.ok(rendered.includes(expected), file + ': ' + state);
      assert.ok(!rendered.includes('unexpected_state'), 'unknown backend states are not shown raw');
      if (['pending', 'running', 'failed'].includes(state))
        assert.match(rendered, /сохранённые результаты предыдущей проверки/);
      assert.ok(flatten(ui.get(target)).some(x => x.href === '/me/cases/' + row.reg_number),
        file + ': retained case stays accessible');
    }
    row = {...row, legal_review_state: 'pending', documents_checked: 0, violations: 0};
    await ui.api.refresh();
    assert.doesNotMatch(words(ui.get(target)), /результаты предыдущей проверки/,
      'initial queue must not claim previous results');
    if (file === 'account_site_ui_v1.py') {
      ui.get('eis223-cabinet').hidden = true;
      ui.get('eis223-toggle-cabinet').listeners.click();
      await flush();
      ui.get('eis223-toggle-cabinet').listeners.click();
      ui.get('eis223-toggle-cabinet').listeners.click();
      await flush();
    } else {
      await ui.get('manual-refresh').onclick();
    }
    assert.ok(ui.calls.every(x => x.method === 'GET'), file + ': refresh/reopen must not mutate');
  }
}

async function testCaseLegalQueueKeepsFindingsAndUsesReadOnlyRefresh() {
  let data = {audit: {state: 'pending', documents_read: 2}, findings_count: 1,
    findings: [{title: 'Сохранённое нарушение', evidence: 'Сохранённое доказательство'}]};
  const ui = harness(embedded('v369_case_ui.py', 'SCRIPT=').replace('__REG__', '"32616412317"'),
    'load', async () => data);
  await flush();
  for (const [state, read, label] of [
    ['pending', 2, 'Юридическая проверка в очереди'],
    ['running', 2, 'Идёт юридическая проверка'],
    ['failed', 2, 'Ошибка юридической проверки'],
    ['completed', 2, 'Юридическая проверка завершена'],
    ['completed', 0, 'Юридическая проверка не завершена: нет проверенных документов'],
    ['unknown', 0, 'Юридическая проверка ещё не завершена'],
    ['no_readable_documents', 0, 'Не удалось прочитать документы для юридической проверки'],
  ]) {
    data = {...data, audit: {state, documents_read: read}};
    await ui.api.load();
    assert.equal(ui.get('case-audit').textContent, label);
    assert.match(words(ui.get('case-finding-entries')), /Сохранённое доказательство/);
    if (['pending', 'running', 'failed'].includes(state))
      assert.match(ui.get('case-note').textContent, /результаты предыдущей проверки/);
  }
  assert.ok(ui.calls.every(x => x.method === 'GET'), 'case opening/refresh cannot queue a new audit');
}

async function testWorkbenchAutomaticReviewHasNoManualMutation() {
  const source = embedded('v370_staff_ui.py', 'JS=');
  assert.doesNotMatch(read('v370_staff_ui.py'), /staff-recheck/,
    'remove both the obsolete button and its unconditional binding');
  assert.doesNotMatch(source, /\/recheck/);
  assert.match(read('v370_staff_ui.py'), /staff-automatic-review-note/);
  const ui = harness(source, 'loadCase,loadProgress', async call => {
    if (call.url.endsWith('/progress')) return {available: true, checks: [{
      reg_number: manualRows[0].reg_number, stage: 'Юридическая проверка в очереди',
      documents_checked: 2, violations: 1,
      previous_results_note: 'Показаны сохранённые результаты предыдущей проверки.',
    }]};
    return staffResponse(call);
  });
  await flush();
  ui.get('staff-reg').value = manualRows[0].reg_number;
  await ui.api.loadCase(); await ui.api.loadCase();
  await ui.get('staff-case-load').onclick();
  await ui.get('staff-refresh').listeners.click();
  await ui.api.loadProgress();
  assert.match(words(ui.get('staff-progress-list')), /сохранённые результаты предыдущей проверки/);
  assert.ok(ui.calls.every(x => x.method === 'GET'), 'workbench reopening/refresh never rechecks');
  assert.equal(ui.get('staff-case-link').href, '/me/cases/' + manualRows[0].reg_number);
  assert.equal(ui.get('staff-case-download').href, '/me/workbench/' + manualRows[0].reg_number + '/export.zip');
  assert.equal(typeof ui.get('staff-bulk-form').onsubmit, 'function', 'initial bulk intake retained');
}

const reviewKey = 'a'.repeat(64), reviewReg = '32616412317';
const findingFixture = () => ({finding_key: reviewKey, title: 'Нарушение', law: '223-ФЗ ст. 4 ч. 12',
  evidence: 'Проверенное доказательство', document: 'Протокол.pdf', warnings: [], review: null, submission: null});
function reviewHarness(responder) {
  const ui = harness(embedded('v382_finding_review_ui.py', 'SCRIPT ='), 'control,load,send', responder);
  ui.box = ui.api.control(reviewReg, reviewKey);
  ui.context.document.querySelectorAll = () => [ui.box];
  ui.submit = () => flatten(ui.box).find(x => x.tagName === 'BUTTON' && x.textContent.includes('руководителю'));
  ui.words = () => flatten(ui.box).map(x => x.textContent).join(' ');
  return ui;
}
async function testFindingSubmissionLifecycleAndNoDuplicate() {
  const held = deferred(); let row = findingFixture();
  const ui = reviewHarness(async call => {
    if (call.method === 'POST') {
      await held.promise; row = {...row, submission: {status: 'submitted'}};
      return {status: 'submitted', already_submitted: false};
    }
    return {findings: [row], manager_assigned: true};
  });
  await ui.api.load(reviewReg);
  assert.equal(ui.submit().textContent, 'Направить на согласование руководителю');
  assert.equal(ui.submit().disabled, false);
  const sending = ui.api.send(reviewReg, reviewKey);
  await ui.api.send(reviewReg, reviewKey);
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 1, 'double click makes one submission');
  assert.ok(flatten(ui.box).some(x => x.tagName === 'BUTTON' && x.textContent === 'Отправляем…' && x.disabled));
  held.resolve(); await sending;
  assert.match(ui.words(), /Отправлено на согласование/);
  assert.equal(ui.submit().disabled, true);
  row = {...row, submission: {status: 'rejected'}, review: {decision: 'rejected', comment: '<script>Причина отказа</script>'}};
  await ui.api.load(reviewReg);
  assert.match(ui.words(), /Комментарий руководителя: <script>Причина отказа<\/script>/);
  assert.equal(ui.submit().disabled, true);
  await ui.api.send(reviewReg, reviewKey);
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 1, 'final denial cannot be resubmitted');
  assert.ok(!ui.words().includes('повторно'));
}
async function testFindingMissingManagerStaleAndErrors() {
  let reply = {findings: [findingFixture()], manager_assigned: false};
  const ui = reviewHarness(async () => reply);
  await ui.api.load(reviewReg);
  assert.equal(ui.submit().disabled, true); assert.match(ui.words(), /Руководитель не назначен/);
  await ui.api.send(reviewReg, reviewKey);
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 0);
  reply = {findings: [], manager_assigned: true}; await ui.api.load(reviewReg);
  assert.equal(ui.submit(), undefined); assert.match(ui.words(), /Версия доказательства изменилась/);
  reply = {httpStatus: 503, body: {detail: 'Сервис недоступен'}}; await ui.api.load(reviewReg);
  assert.match(ui.words(), /Сервис недоступен/);
  assert.ok(flatten(ui.box).some(x => x.tagName === 'BUTTON' && !x.disabled), 'failed GET can be retried');
}
async function testFindingMutationOutranksInFlightRead() {
  const held = deferred(); let reads = 0, submitted = false;
  const ui = reviewHarness(async call => {
    if (call.method === 'POST') {submitted = true; return {status: 'submitted', already_submitted: false};}
    if (++reads === 2) return held.promise;
    return {findings: [{...findingFixture(), submission: submitted ? {status: 'submitted'} : null}], manager_assigned: true};
  });
  await ui.api.load(reviewReg);
  const stale = ui.api.load(reviewReg), sending = ui.api.send(reviewReg, reviewKey);
  await flush(); assert.equal(reads, 2);
  held.resolve({findings: [findingFixture()], manager_assigned: true});
  await Promise.all([stale, sending]);
  assert.equal(reads, 3, 'mutation queues fresh read after stale GET');
  assert.equal(ui.submit().disabled, true); assert.match(ui.words(), /Отправлено на согласование/);
}
async function testThematicFindingContextAndNoDuplicate() {
  const batch='a'.repeat(24),other='b'.repeat(24),held=deferred();let submitted=false;
  const ui=harness(embedded('v382_finding_review_ui.py','SCRIPT ='),'control,load,send',async call=>{
    assert.ok(call.url.endsWith('?batch='+batch)||call.url.endsWith('?batch='+other));
    if(call.method==='POST'){assert.ok(call.url.endsWith('/submit?batch='+batch));await held.promise;submitted=true;return {status:'submitted'}}
    return {findings:[{...findingFixture(),submission:submitted&&call.url.endsWith(batch)?{status:'submitted'}:null}],manager_assigned:true};
  });
  const first=ui.api.control(reviewReg,reviewKey,batch),second=ui.api.control(reviewReg,reviewKey,other);
  ui.context.document.querySelectorAll=()=>[first,second];
  await Promise.all([ui.api.load(reviewReg,false,batch),ui.api.load(reviewReg,false,other)]);
  const sending=ui.api.send(reviewReg,reviewKey,batch);
  await ui.api.send(reviewReg,reviewKey,batch);
  assert.equal(ui.calls.filter(x=>x.method==='POST').length,1);
  held.resolve();await sending;
  const button=box=>flatten(box).find(x=>x.tagName==='BUTTON'&&x.textContent.includes('руководителю'));
  assert.equal(button(first).disabled,true);
  assert.equal(button(second).disabled,false,'another batch context must not inherit stale local state');
}
async function testMspRecheckButtonScopeAndSingleMutation() {
  const batch = 'a'.repeat(24), otherKey = 'b'.repeat(64), held = deferred();
  const eligible = {...findingFixture(), code: '223-SME-BID-SECURITY-OVER-TWO-PERCENT', effective_status: 'confirmed'};
  const unrelated = {...findingFixture(), finding_key: otherKey, code: '223-4-12', effective_status: 'confirmed'};
  const ui = harness(embedded('v382_finding_review_ui.py', 'SCRIPT ='), 'control,load,send,recheckMsp', async call => {
    if (call.method === 'POST') { await held.promise; return {effective_status: 'superseded'}; }
    return {findings: [eligible, unrelated], manager_assigned: true};
  });
  let reloads = 0; ui.context.location.reload = () => { reloads++; };
  const thematic = ui.api.control(reviewReg, reviewKey, batch);
  const general = ui.api.control(reviewReg, reviewKey);
  const other = ui.api.control(reviewReg, otherKey, batch);
  ui.context.document.querySelectorAll = () => [thematic, general, other];
  await Promise.all([ui.api.load(reviewReg, false, batch), ui.api.load(reviewReg)]);
  const recheck = box => flatten(box).find(x => x.tagName === 'BUTTON' && x.textContent === 'Перепроверить признак МСП');
  assert.ok(recheck(thematic));
  assert.equal(recheck(general), undefined, 'general findings never offer targeted thematic recheck');
  assert.equal(recheck(other), undefined, 'other legal codes never offer the SME-only action');
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 0, 'loading a card never triggers reassessment');
  await ui.api.recheckMsp(reviewReg, reviewKey, '');
  await ui.api.recheckMsp(reviewReg, otherKey, batch);
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 0);
  const click = recheck(thematic).onclick, pending = click();
  await click();
  const writes = ui.calls.filter(x => x.method === 'POST');
  assert.equal(writes.length, 1, 'rapid repeat clicks cannot duplicate the recheck');
  assert.equal(writes[0].url, `/me/thematic/${batch}/${reviewReg}/findings/${reviewKey}/msp-recheck`);
  assert.equal(writes[0].options.credentials, 'same-origin');
  assert.equal(writes[0].options.cache, 'no-store');
  assert.ok(flatten(thematic).some(x => x.tagName === 'BUTTON' && x.textContent === 'Перепроверяем МСП…' && x.disabled));
  held.resolve(); await pending;
  assert.equal(reloads, 1, 'successful recheck reloads canonical counts and evidence state');
}
async function testMspInvalidatedSubmissionAndRecheckFailureRetry() {
  const batch = 'a'.repeat(24);
  let row = {...findingFixture(), code: '223-SME-BID-SECURITY-OVER-TWO-PERCENT',
    effective_status: 'superseded', reassessment: {reason: 'Предел 2% неприменим. Исходное доказательство сохранено.'}};
  let fail = true, reloads = 0;
  const ui = harness(embedded('v382_finding_review_ui.py', 'SCRIPT ='), 'control,load,send,recheckMsp', async call => {
    if (call.method === 'POST') return fail ? {httpStatus: 503, body: {detail: 'Источник временно недоступен'}} : {effective_status: 'confirmed'};
    return {findings: [row], manager_assigned: true};
  });
  ui.context.location.reload = () => { reloads++; };
  const box = ui.api.control(reviewReg, reviewKey, batch);
  ui.context.document.querySelectorAll = () => [box];
  const button = text => flatten(box).find(x => x.tagName === 'BUTTON' && x.textContent.includes(text));
  const words = () => flatten(box).map(x => x.textContent).join(' ');
  for (const effective_status of ['superseded', 'requires_review']) {
    row = {...row, effective_status}; await ui.api.load(reviewReg, false, batch);
    assert.ok(button('руководителю').disabled, effective_status + ' cannot be submitted');
    await ui.api.send(reviewReg, reviewKey, batch);
  }
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 0);
  row = {...row, submission: {status: 'approved'}, review: {decision: 'approved', comment: 'Проверено ранее'}};
  await ui.api.load(reviewReg, false, batch);
  assert.match(words(), /Согласовано руководителем/);
  assert.match(words(), /Проверено ранее/);
  assert.ok(!button('Перепроверить признак').disabled, 'an old approval does not prevent safe reassessment');
  await button('Перепроверить признак').onclick();
  assert.equal(reloads, 0, 'failed reassessment stays on the card');
  assert.match(words(), /Источник временно недоступен/);
  assert.match(words(), /Согласовано руководителем/);
  assert.ok(!button('Перепроверить признак').disabled, 'failed request is retryable');
  assert.ok(button('руководителю').disabled);
  fail = false; await button('Перепроверить признак').onclick();
  assert.equal(reloads, 1);
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 2);
}
async function testManagerFindingQueueAndVersionedDecisions() {
  let row = {...findingFixture(), submission: {status: 'submitted', version: 7}}, rejected = false;
  const ui = harness(embedded('v368_manager_ui.py', 'SCRIPT='), 'loadFindings,loadFindingQueue', async call => {
    if (call.url.includes('/decision') && call.method === 'POST') {
      const body = JSON.parse(call.options.body);
      assert.equal(body.submission_version, 7); assert.equal(body.decision, 'rejected');
      assert.equal(body.comment, 'Недостаточное доказательство');
      rejected = true; row = {...row, review: {decision: 'rejected', comment: body.comment}, submission: {status: 'rejected', version: 7}};
      return {decision: 'rejected'};
    }
    if (call.url === '/manager/finding-submissions') return [{...row, reg_number: reviewReg, user_id: 'employee-1',
      employee: 'Сотрудник', status: rejected ? 'rejected' : 'submitted'}];
    if (call.url.startsWith('/manager/findings/')) return {findings: [row]};
    if (call.url === '/manager/dashboard') return {tasks_by_day: []};
    if (call.url.endsWith('/options')) return {categories: {}, legal_forms: {}};
    if (call.url.endsWith('/quality')) return {issues: []};
    return [];
  });
  await flush(); await flush();
  assert.equal(ui.get('mgr-submission-items').children.length, 1);
  ui.get('mgr-finding-worker').value = 'employee-1'; ui.get('mgr-finding-reg').value = reviewReg;
  await ui.api.loadFindings();
  let buttons = flatten(ui.get('mgr-finding-items')).filter(x => x.tagName === 'BUTTON');
  assert.deepEqual(buttons.map(x => x.textContent), ['Согласовать', 'Отказать']);
  ui.context.prompt = () => 'Нет'; await buttons[1].onclick();
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 0, 'short refusal reason must not POST');
  ui.context.prompt = () => 'Недостаточное доказательство'; await buttons[1].onclick();
  buttons = flatten(ui.get('mgr-finding-items')).filter(x => x.tagName === 'BUTTON');
  assert.equal(buttons.length, 2); assert.ok(buttons.every(x => x.disabled), 'terminal decision is read-only');
  assert.match(ui.get('mgr-submission-note').textContent, /Ожидают решения: 0/);
}

async function testAdminTeamSaveCancelRetryAndInactive() {
  let reply=deferred(),reloads=0;
  const ui=harness('(function(){'+embedded('account_admin_team_ui.py','TEAM_SCRIPT =')+'})();','',()=>reply.promise);
  const person={id:'e',email:'employee@example.test',role:'user',status:'approved',manager_id:'m1'};
  const leaders=[{id:'m1',email:'one@example.test',role:'manager',status:'approved'},
    {id:'m2',email:'two@example.test',role:'manager',status:'approved'},
    {id:'m3',email:'inactive@example.test',role:'manager',status:'blocked'}];
  const status=new Element();
  const root=ui.context.eis223TeamControl(person,leaders,status,async()=>{reloads++});
  const nodes=flatten(root),select=nodes.find(x=>x.tagName==='SELECT'),save=nodes.find(x=>x.tagName==='BUTTON');
  assert.equal(select.value,'m1');assert.ok(save.disabled);
  assert.equal(select.children.length,3,'only active managers plus unassigned');
  select.value='m2';select.onchange();assert.ok(!save.disabled);
  ui.context.confirm=()=>false;await save.onclick();assert.equal(ui.calls.length,0);
  ui.context.confirm=()=>true;
  const task=save.onclick();await flush();assert.ok(save.disabled&&select.disabled);
  await save.onclick();assert.equal(ui.calls.length,1,'duplicate save blocked');
  reply.resolve({employee:'e',manager:'m2'});await task;
  assert.equal(person.manager_id,'m2');assert.equal(reloads,1);assert.ok(save.disabled);
  select.value='';select.onchange();reply=deferred();const failure=save.onclick();
  reply.resolve({httpStatus:409,body:{detail:'Account changed'}});await failure;
  assert.match(status.textContent,/Account changed/);assert.ok(!save.disabled&&!select.disabled);
  assert.equal(person.manager_id,'m2');assert.equal(reloads,1);
  reply=deferred();const retry=save.onclick();reply.resolve({employee:'e',manager:null});await retry;
  assert.equal(person.manager_id,null);assert.equal(reloads,2);
  const blocked=ui.context.eis223TeamControl({...person,status:'blocked',manager_id:'m3'},leaders,status);
  const inactiveSelect=flatten(blocked).find(x=>x.tagName==='SELECT');
  assert.equal(inactiveSelect.value,'m3');assert.ok(inactiveSelect.children.filter(x=>x.value).every(x=>x.disabled));
  assert.equal(ui.context.eis223TeamControl({...person,role:'manager'},leaders,status).children.length,0);
}
async function testManagerSelfAttachAndRefresh() {
  let attached=false,write=deferred();
  const source=embedded('v368_manager_ui.py','SCRIPT=').replace('__ROLE__','"manager"');
  const ui=harness(source,'reload',async call=>{
    if(call.url==='/manager/team/e/attach'){await write.promise;attached=true;return {manager:'m',employee:'e'}}
    if(call.url==='/manager/team/available')return attached?[]:[{id:'e',email:'staff@example.test',full_name:'Staff'}];
    if(call.url==='/manager/team')return attached?[{id:'e',email:'staff@example.test',role:'user',status:'approved'}]:[];
    if(call.url==='/manager/dashboard')return {tasks_by_day:[]};
    if(call.url.endsWith('/options'))return {categories:{},legal_forms:{}};
    if(call.url.endsWith('/quality'))return {issues:[]};
    return [];
  });
  await flush();await flush();
  const select=ui.get('mgr-available-employee'),button=ui.get('mgr-attach-self');
  assert.equal(select.children.length,2);assert.ok(button.disabled);
  select.value='e';select.onchange();ui.context.confirm=()=>false;await button.onclick();
  assert.equal(ui.count('/manager/team/e/attach'),0);
  ui.context.confirm=()=>true;const task=button.onclick();await flush();
  await ui.api.reload();assert.ok(button.disabled,'refresh cannot unlock pending attachment');
  await button.onclick();assert.equal(ui.count('/manager/team/e/attach'),1);
  write.resolve();await task;
  assert.equal(select.children.length,1);assert.ok(button.disabled);
  assert.equal(ui.get('mgr-team-rows').children.length,1);
  assert.match(ui.get('mgr-attach-status').textContent,/Сотрудник прикреплён/);
}

(async () => {
  for (const test of [testManualFiltersAndRefreshRace, testThematicFiltersAndDeniedPolling,
    testHistoricalAdoptionQueuesFreshRead, testStaffRequestsAndAsyncForms, testParallelCaseReadsIgnoreOlderResponses,
    testCaseSwitchDisablesOldDraftAndControls, testDirtyDraftSurvivesRefreshAndCaseSwitch,
    testOldSubmissionCannotResetNewCaseForm, testAutosavesStayOrdered, testOlderCaseSearchesCannotRenderIntoNewCase,
    testDashboardSharingAndMutationRefresh, testManagerSearchAndReloadQueue,
    testLegalStatusSeparateFromCompletedDownload, testCaseLegalQueueKeepsFindingsAndUsesReadOnlyRefresh,
    testWorkbenchAutomaticReviewHasNoManualMutation,
    testFindingSubmissionLifecycleAndNoDuplicate, testFindingMissingManagerStaleAndErrors,
    testFindingMutationOutranksInFlightRead, testThematicFindingContextAndNoDuplicate,
    testMspRecheckButtonScopeAndSingleMutation, testMspInvalidatedSubmissionAndRecheckFailureRetry,
    testManagerFindingQueueAndVersionedDecisions,
    testAdminTeamSaveCancelRetryAndInactive, testManagerSelfAttachAndRefresh]) {
    await test(); console.log('PASS', test.name);
  }
  console.log('Account frontend request/race regressions: 24 scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
