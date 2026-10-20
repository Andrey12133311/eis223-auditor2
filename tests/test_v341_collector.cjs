// Real HTML layouts, Unicode monetary labels and missing data; no EIS calls.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {parseHTML}=require('linkedom');
const source=fs.readFileSync(require('node:path').join(__dirname,'../scripts/v299_browser_collector.js'),'utf8');
const labels=source.slice(source.indexOf('  function valueByLabel('),source.indexOf('  function customerFromDocument('));
const prices=source.slice(source.indexOf('  function cardPricesFromDocument('),source.indexOf('  async function reportOfficialPrice('));
const api=vm.runInNewContext(labels+prices+';({cardPriceFromDocument,cardPricesFromDocument,cardFactsFromDocument})');
const reg='32616471929';
const make=body=>parseHTML('<html><body><h1>Закупка № '+reg+'</h1>'+body+'</body></html>').document;
const row=(label,value)=>'<div><span class="section__title">'+label+'</span><span class="section__info">'+value+'</span></div>';
for(const [label,kind] of [
 ['Начальная (максимальная) цена договора','nmcd'],
 ['Начальная максимальная цена договора','nmcd'],
 ['Начальная (максимальная) цена договора (цена лота)','nmcd'],
 ['Максимальное значение цены договора','maximum_contract_value'],
 ['Максимальная цена договора','maximum_contract_value'],
 ['Стоимость предмета закупки','subject_cost'],
 ['Стоимость закупки','subject_cost'],
 ['Цена договора','contract_price'],
 ['Общая стоимость договора','contract_price'],
 ['Цена лота','lot_price'],
 ['Начальная сумма цен единиц товара','total_unit_prices'],
 ['Цена единицы товара','unit_price'],
 ['Начальная (максимальная) цена единицы товара','unit_price']]){
 const out=api.cardPriceFromDocument(make(row(label,'41 081,60 Российский рубль')),reg);
 assert.equal(out?.price,'41081.60',label);assert.equal(out.price_kind,kind,label);
}
assert.equal(api.cardPriceFromDocument(make(row('Стоимость предмета закупки','0,00 ₽')),reg).price,'0.00');
assert.equal(api.cardPriceFromDocument(make('<table><tr><th>Стоимость предмета закупки</th><td>120 000 рублей 45 копеек</td></tr></table>'),reg).price,'120000.45');
assert.equal(api.cardPriceFromDocument(make('<span class="title">Стоимость предмета закупки: 999,50 руб.</span>'),reg).price,'999.50');
for(const label of ['Цена победителя','Обеспечение заявки','Обеспечение исполнения договора',
 'Начальная (максимальная) цена','Начальная максимальная цена','Начальная цена лота'])
 assert.equal(api.cardPriceFromDocument(make(row(label,'300 000 ₽')),reg),null,label);
for(const value of ['','—','100-200 ₽','31.12.2026','1000 USD'])
 assert.equal(api.cardPriceFromDocument(make(row('НМЦД',value)),reg),null,value);
assert.equal(api.cardPriceFromDocument(make(row('НМЦД','1000 ₽')),'32616471928'),null);
const multi=make(row('Цена лота','10 000 ₽')+row('Цена лота','20 000 ₽'));
assert.equal(api.cardPriceFromDocument(multi,reg),null);
assert.equal(api.cardPricesFromDocument(multi,reg).length,2);
const all=make(row('Максимальное значение цены договора','5 000 000 ₽')+row('Цена единицы товара','1 000 ₽'));
assert.equal(api.cardPriceFromDocument(all,reg).price_kind,'maximum_contract_value');
assert.equal(api.cardPricesFromDocument(all,reg).length,2);
const facts=api.cardFactsFromDocument(make(row('Способ закупки','Аукцион в электронной форме')+
 row('Дата окончания подачи заявок','22.10.2026 19:00')+row('Формула цены договора','Ц = Т × V')),reg);
assert.equal(facts.fields.submission_end,'2026-10-22');
assert.equal(facts.fields.method,'Аукцион в электронной форме');
assert.equal(facts.price_formula,'Ц = Т × V');
assert.match(source,/job\.metadata_only/);
console.log('V341 collector DOM: 30 monetary/date/source/lot cases passed');
