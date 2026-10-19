/* Exercise the actual embedded thematic/manager scripts without a live service.
 * Run: node tests/test_thematic_period_ui.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = file => fs.readFileSync(path.join(__dirname, '..', 'scripts', file), 'utf8');
const embedded = (file, assignment) => read(file).split(assignment)[1].split('<script>')[1].split('</script>')[0];
const flush = () => new Promise(resolve => setImmediate(resolve));
const textOf = node => node.textContent + (node.children || []).map(textOf).join(' ');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.nodeType = 1; this.children = [];
    this.value = ''; this.textContent = ''; this.style = {}; this.dataset = {};
    this.checked = false; this.disabled = false; this.listeners = {}; this.attrs = {};
    this.valid = true; this.formValues = {}; this.classList = {add() {}, remove() {}, toggle() {}};
    this.elements = {namedItem: name => this.querySelector(name)};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this.attrs[name] = value; }
  getAttribute(name) { return this.attrs[name]; }
  querySelectorAll(selector) {
    const children = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    return selector === 'input:checked' ? children.filter(child => child.tagName === 'INPUT' && child.checked) : children;
  }
  querySelector(selector) {
    this.queries ||= {};
    return this.queries[selector] ||= new Element(selector.includes('button') ? 'button' : 'input');
  }
  focus() { this.focused = true; }
  checkValidity() { return this.valid; }
  reportValidity() { this.reported = true; return this.valid; }
}

function harness(source, responder, exposed = '') {
  const nodes = new Map(), calls = [];
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const context = {
    console, URL, URLSearchParams, AbortController,
    location: {hash: '', pathname: '/thematic', search: '', assign() {}},
    document: {
      hidden: false, getElementById: get, querySelector: selector => get(selector.replace(/^#/, '')),
      querySelectorAll: () => [], createElement: tag => new Element(tag),
      createTextNode: value => Object.assign(new Element('#text'), {textContent: value}),
    },
    alert: value => { throw new Error('Unexpected alert: ' + value); }, confirm: () => true,
    addEventListener() {}, setInterval() {}, setTimeout() {}, clearTimeout() {},
    FormData: class { constructor(form) { this.values = form.formValues; } entries() { return Object.entries(this.values); } },
    fetch: async (url, options = {}) => {
      const call = {url, method: options.method || 'GET', options}; calls.push(call);
      const answer = await responder(call);
      const status = answer?.httpStatus || 200;
      return {ok: status < 400, status, json: async () => answer?.httpStatus ? answer.body : answer};
    },
  };
  context.window = context;
  source = source.replace('__CATEGORIES__', '{"dates":"Сроки"}')
    .replace('__LEGAL_FORMS__', '{"ООО":"Общество"}').replace('__ROLE__', '"admin"');
  if (exposed) {
    const end = source.lastIndexOf('})();');
    assert.ok(end >= 0);
    source = source.slice(0, end) + `globalThis.testAPI={${exposed}};\n` + source.slice(end);
  }
  vm.runInNewContext(source, context, {timeout: 1000});
  return {get, calls, context, api: context.testAPI, posts: () => calls.filter(call => call.method === 'POST')};
}

async function testCreateRangeAndValidation() {
  const markup = read('v342_thematic_queue.py').split('PAGE="""')[1].split('\nDASHBOARD_CLEANUP')[0];
  for (const side of ['from', 'to']) {
    const input = markup.match(new RegExp('<input[^>]*id="thematic-date-' + side + '"[^>]*>'))?.[0];
    assert.ok(input, side);
    assert.match(input, /type="date"/);
    assert.ok(input.includes('name="date_' + side + '"'));
    assert.doesNotMatch(input, /\brequired\b/);
  }
  const batch = {id: 'a'.repeat(24), status: 'active', created_at: '2026-10-19T00:00:00Z', items: [], purchases: 2, violations: 1};
  let rejection = null, held = null;
  const ui = harness(embedded('v342_thematic_queue.py', 'PAGE="""'), async call => {
    if (call.method === 'POST') {
      if (held) await held;
      if (rejection) return {httpStatus: 422, body: {detail: rejection}};
      Object.assign(batch, JSON.parse(call.options.body));
      return {id: batch.id};
    }
    return call.url.endsWith('/history') ? {batches: [batch]} : batch;
  });
  ui.get('cats').children[0].children[0].checked = true;
  await flush();
  assert.match(textOf(ui.get('history')), /За всё время/, 'legacy queues retain an explicit unbounded label');
  const start = ui.get('start');
  for (const [from, to] of [
    ['2026-10-01', '2026-10-19'], ['2026-10-19', '2026-10-19'],
    ['2026-10-01', ''], ['', '2026-10-19'], ['', ''],
  ]) {
    ui.get('thematic-date-from').value = from; ui.get('thematic-date-to').value = to;
    const before = ui.posts().length;
    await start.onclick(); await flush();
    assert.equal(ui.posts().length, before + 1);
    const payload = JSON.parse(ui.posts().at(-1).options.body);
    assert.equal(payload.date_from, from); assert.equal(payload.date_to, to);
    assert.deepEqual(payload.categories, ['dates']); assert.deepEqual(payload.legal_forms, ['ООО']);
    assert.equal(start.disabled, false);
    if (from && to) {
      const period = `С ${from.split('-').reverse().join('.')} по ${to.split('-').reverse().join('.')} включительно`;
      assert.ok(textOf(ui.get('history')).includes(period));
      assert.ok(textOf(ui.get('out')).includes(period), 'saved period is echoed by the selected queue');
    }
  }
  ui.get('thematic-date-from').value = '2026-10-19'; ui.get('thematic-date-to').value = '2026-10-01';
  const before = ui.posts().length;
  await start.onclick();
  assert.equal(ui.posts().length, before, 'a reversed range must never create a queue');
  assert.match(ui.get('thematic-start-error').textContent, /не может быть позже/);
  assert.equal(ui.get('thematic-start-error').hidden, false);
  ui.get('thematic-period-clear').onclick();
  assert.equal(ui.get('thematic-date-from').value, ''); assert.equal(ui.get('thematic-date-to').value, '');
  assert.equal(ui.get('thematic-start-error').hidden, true);
  ui.get('thematic-date-from').valid = false;
  await start.onclick();
  assert.equal(ui.posts().length, before); assert.equal(ui.get('thematic-date-from').reported, true);
  ui.get('thematic-date-from').valid = true;
  rejection = 'Некорректный период размещения в ЕИС';
  await start.onclick();
  assert.equal(ui.get('thematic-start-error').textContent, rejection);
  assert.equal(start.disabled, false, 'an API validation error leaves the form usable');
  rejection = null;
  let release; held = new Promise(resolve => { release = resolve; });
  const pending = start.onclick(), attempts = ui.posts().length;
  await start.onclick(); assert.equal(ui.posts().length, attempts, 'repeat clicks cannot create duplicate campaigns');
  release(); held = null; await pending; await flush();
}

async function testSavedCabinetPeriods() {
  const rows = [{id: '1', categories: ['dates'], created_at: '2026-10-19', date_from: '2026-01-01', date_to: '2026-03-31'}];
  const ui = harness(embedded('account_pages_v1.py', 'THEMATIC ='), async call =>
    call.url.endsWith('/unassigned') ? {httpStatus: 403, body: {}} : {batches: rows}, 'renderThematic');
  await flush();
  assert.match(textOf(ui.get('real-thematic-list')), /Дата размещения в ЕИС: С 01\.01\.2026 по 31\.03\.2026 включительно/);
  const source = read('account_pages_v1.py').split('THEMATIC =')[1].split('MEMOS =')[0];
  assert.match(source, /Дата запуска с/); assert.match(source, /Дата запуска по/);
  assert.match(source, /href="\/thematic">Новая тематическая проверка/);
}

async function testManagerCampaignRange() {
  const ui = harness(embedded('v368_manager_ui.py', 'SCRIPT='), async call => {
    if (call.method === 'POST') return {status: 'active'};
    if (call.url === '/me/profile') return {full_name: 'Admin'};
    if (call.url === '/manager/dashboard') return {tasks_by_day: []};
    if (call.url.endsWith('/mail/status')) return {configured: true};
    if (call.url.endsWith('/options')) return {categories: {dates: 'Сроки'}, legal_forms: {'ООО': 'Общество'}};
    if (call.url.endsWith('/quality')) return {issues: []};
    return [];
  }, 'thematicPeriod');
  await flush();
  for (const id of ['mgr-goal-categories', 'mgr-goal-forms']) {
    const input = new Element('input'); input.checked = true; input.value = id.endsWith('categories') ? 'dates' : 'ООО';
    ui.get(id).replaceChildren(input);
  }
  const form = ui.get('mgr-goal-form');
  for (const [from, to] of [['2026-10-01', '2026-10-19'], ['2026-10-01', ''], ['', '2026-10-19'], ['', '']]) {
    form.formValues = {title: 'Проверка закупок', target_violations: '10', date_from: from, date_to: to};
    await form.onsubmit({currentTarget: form, preventDefault() {}});
    const payload = JSON.parse(ui.posts().at(-1).options.body);
    assert.equal(payload.date_from, from || null); assert.equal(payload.date_to, to || null);
  }
  const before = ui.posts().length;
  form.formValues = {target_violations: '10', date_from: '2026-10-19', date_to: '2026-10-01'};
  await form.onsubmit({currentTarget: form, preventDefault() {}});
  assert.equal(ui.posts().length, before);
  assert.match(ui.get('mgr-goal-status').textContent, /не может быть позже/);
  ui.get('mgr-goal-date-from').value = '2026-10-01'; ui.get('mgr-goal-date-to').value = '2026-10-19';
  ui.get('mgr-goal-period-clear').onclick();
  assert.equal(ui.get('mgr-goal-date-from').value, ''); assert.equal(ui.get('mgr-goal-date-to').value, '');
  assert.equal(ui.api.thematicPeriod({}), 'За всё время');
  assert.equal(ui.api.thematicPeriod({date_from: '2026-10-19'}), 'С 19.10.2026');
  assert.equal(ui.api.thematicPeriod({date_to: '2026-10-19'}), 'По 19.10.2026 включительно');
}

async function testSavedOpeningRecoveryAndNavigation() {
  const a='a'.repeat(24), b='b'.repeat(24);
  let fail=true, resolveOld;
  const ui=harness(embedded('v342_thematic_queue.py','PAGE="""'),async call=>{
    if(call.url.endsWith('/history'))return {batches:[a,b].map(id=>({id,created_at:'2026-10-19',purchases:819,violations:1,status:'active'}))};
    if(fail)return {httpStatus:503,body:{detail:'База занята. Повторите открытие.'}};
    if(call.url.endsWith(a))return await new Promise(resolve=>{resolveOld=resolve});
    return {id:b,status:'active',items:[],counts:{completed:42}};
  });
  // Browser location normalizes assignments to a leading #; exercise real clicks.
  let hash='';Object.defineProperty(ui.context.location,'hash',{get:()=>hash,set:value=>{hash=value&&!value.startsWith('#')?'#'+value:value}});
  await flush();
  const first=ui.get('history').children[0].children[0];
  first.onclick({preventDefault(){}});
  assert.match(ui.get('out').textContent,/Загрузка/, 'click immediately changes result pane');
  await flush();
  assert.match(textOf(ui.get('thematic-load-status')),/База занята/);
  assert.match(textOf(ui.get('thematic-load-status')),/Повторить открытие/);
  fail=false;
  ui.get('thematic-load-status').children.at(-1).onclick();
  const count=ui.calls.filter(c=>c.url.endsWith(a)).length;
  first.onclick({preventDefault(){}});
  assert.equal(ui.calls.filter(c=>c.url.endsWith(a)).length,count,'same queue single flight');
  ui.get('history').children[1].children[0].onclick({preventDefault(){}});
  await flush();
  assert.match(textOf(ui.get('out')),/42/);
  resolveOld({id:a,status:'active',counts:{completed:999},items:[]});await flush();
  assert.doesNotMatch(textOf(ui.get('out')),/999/,'late response cannot replace new selection');
  assert.match(textOf(ui.get('out')),/42/);
  assert.equal(ui.context.refresh,undefined,'page helpers cannot collide with injected scripts');
}

(async () => {
  await testSavedOpeningRecoveryAndNavigation();
  await testCreateRangeAndValidation();
  await testSavedCabinetPeriods();
  await testManagerCampaignRange();
  console.log('THEMATIC_PERIOD_UI_OK: real embedded handlers; bounded/one-sided/all-time requests; invalid ranges blocked; saved dates shown; duplicate clicks blocked');
})().catch(error => { console.error(error); process.exitCode = 1; });
