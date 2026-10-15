// Run with linkedom available in NODE_PATH; no browser or EIS network calls.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {parseHTML}=require('linkedom');
const source=fs.readFileSync(path.join(__dirname,'../scripts/v299_browser_collector.js'),'utf8');
const helpers=source.slice(source.indexOf('  function valueByLabel('),
  source.indexOf('  function documentPageCandidates('));
const titleHelper=source.slice(source.indexOf('  function searchTitle('),
  source.indexOf('  // Only certified public EIS file references'));
const api=vm.runInNewContext(helpers+titleHelper+
  ';({customerFromDocument,verifiedSearchCustomer,searchTitle})');
const documentOf=html=>parseHTML('<html><body>'+html+'</body></html>').document;
const results=documentOf(`
<div class="search-registry-entry-block">
 <a href="/epz/order/notice/notice223/common-info.html?noticeId=888">№ 32616467075</a>
 <div><div class="registry-entry__body-title">Заказчик</div>
  <a class="registry-entry__body-href">ООО «Другой заказчик»</a></div>
</div>
<div class="search-registry-entry-block">
 <a href="/epz/order/notice/notice223/common-info.html?noticeId=999">№ 32616467074</a>
 <span>Подача заявок</span>
 <div><div class="registry-entry__body-title">Заказчик</div>
  <a class="registry-entry__body-href">ФГБОУ ВО ТГМУ Минздрава России</a></div>
 <div><div class="registry-entry__body-title">Объект закупки</div>
  <div class="registry-entry__body-value">Поставка реактивов</div></div>
</div>`);
assert.equal(api.verifiedSearchCustomer(results,'32616467074').name,
  'ФГБОУ ВО ТГМУ Минздрава России');
assert.equal(api.searchTitle(results,'32616467074'),'Поставка реактивов');
assert.equal(api.verifiedSearchCustomer(results,'32616467076').name,'');
const card=documentOf(`
<section><h2>Сведения об организаторе</h2>
 <table><tr><td>Наименование организации</td><td>ООО «Организатор»</td></tr>
 <tr><td>ИНН</td><td>2801020845</td></tr></table></section>
<section><h2>Сведения о заказчике</h2>
 <table><tr><td>Наименование организации</td><td>ГАУ СО «АРЧК»</td></tr>
 <tr><td>ИНН:</td><td>6501309746</td></tr></table></section>`);
const buyer=api.customerFromDocument(card,'32616467075');
assert.equal(buyer.name,'ГАУ СО «АРЧК»');
assert.equal(buyer.inn,'6501309746');
const paired=documentOf(`<section><h2>Сведения о заказчике</h2>
 <div><span class="section__title">Наименование (полное) - ПАО «Сахалинэнерго»</span></div>
 <table><tr><td>ИНН / КПП</td><td>6500000024 / 650101001</td></tr></table>
 <div><span class="section__title">Предмет закупки</span><span class="section__info">Поставка сервера</span></div>
 </section>`);
assert.equal(api.customerFromDocument(paired,'32616422163').name,'ПАО «Сахалинэнерго»');
assert.equal(api.customerFromDocument(paired,'32616422163').inn,'6500000024');
assert.equal(api.searchTitle(results,'32616467074'),'Поставка реактивов');
console.log('V314 collector DOM regression: 8 checks passed');
