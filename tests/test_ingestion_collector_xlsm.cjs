// Actual collector functions, synthetic bytes, no network or macro execution.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../scripts/v299_browser_collector.js'),'utf8');
const match=source.match(/  const EXT = ([^;]+);/);
assert.ok(match);
const start=source.indexOf('  function downloadUid(');
const end=source.indexOf('  function originalIntake(',start);
assert.ok(start>=0&&end>start);
const uid='A'.repeat(32),url='https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='+uid;
const bytes=Buffer.from('PK\x03\x04synthetic-zip-central-directory xl/workbook.xml');
const buffer=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);
let downloadName='',mime='application/octet-stream';
const context=vm.createContext({Uint8Array,DataView,TextDecoder,ArrayBuffer,String,Number,Math,Error,
  PAUSE_MS:1,wait:async()=>{},maybeOfficialFile:()=>({uid,url}),
  isRateLimited:()=>false,request:async()=>({ok:true,status:200,arrayBuffer:async()=>buffer,
    headers:{get:key=>({'content-disposition':downloadName?'attachment; filename="'+downloadName+'"':'',
                        'content-type':mime,'content-length':String(bytes.length)}[key]||'')}}),
});
vm.runInContext('const EXT='+match[1]+';'+source.slice(start,end)+
  ';this.EXT=EXT;this.downloadOfficialFile=downloadOfficialFile;this.preparedFileName=preparedFileName;',context);
(async()=>{
  assert.equal(context.EXT.test('Цены.xlsm'),true);
  let item={uid,url,name:'Цены.xlsm'};
  let downloaded=await context.downloadOfficialFile(item);
  assert.equal(downloaded.extension,'.xlsm');
  assert.equal(context.preparedFileName(item,downloaded),'Цены.xlsm');
  downloadName='Цены из ЕИС.xlsm';
  downloaded=await context.downloadOfficialFile({...item,name:'Цены'});
  assert.equal(downloaded.extension,'.xlsm');
  assert.equal(context.preparedFileName(item,downloaded),downloadName);
  downloadName='download';
  downloaded=await context.downloadOfficialFile(item);
  assert.equal(downloaded.extension,'.xlsm'); // generic header cannot erase explicit official filename
  assert.equal(context.preparedFileName(item,downloaded),'Цены.xlsm');
  downloadName='';
  downloaded=await context.downloadOfficialFile({...item,name:'Без расширения'});
  assert.equal(downloaded.extension,'.xlsx'); // ambiguous workbook never guessed as XLSM
  mime='application/vnd.ms-excel.sheet.macroEnabled.12';
  downloaded=await context.downloadOfficialFile({...item,name:'Без расширения'});
  assert.equal(downloaded.extension,'.xlsx'); // HTTP MIME alone is not verified package metadata
  downloadName='официальное.xlsx';
  downloaded=await context.downloadOfficialFile(item);
  assert.equal(downloaded.extension,'.xlsx'); // keep conflicting official download filename conservative
  console.log('XLSM collector: explicit names retained; ambiguous ZIPs and MIME alone never upgraded');
})().catch(error=>{console.error(error);process.exitCode=1});
