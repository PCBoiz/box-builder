const fs=require('fs'); const vm=require('vm');
const ctx={console,String,Number,Math,Object,JSON,parseInt,RegExp}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync('integrations/google_sheets_apps_script.gs','utf8')+'\nthis.buildPlan_=buildPlan_;this.describe_=describe_;this.CONFIG=CONFIG;this.sectionOf_=sectionOf_;this.colShift_=colShift_;this.collectUsedIds_=collectUsedIds_;this.usedIdOf_=usedIdOf_;this.moduleTabNames_=moduleTabNames_;this.usedIdsHtml_=usedIdsHtml_;this.escapeHtml_=escapeHtml_;this.showUsedIds=showUsedIds;this.onOpen=onOpen;this.readRange_=readRange_;this.blockStarts_=blockStarts_;',ctx);
vm.runInContext('this.NO_TOKEN_TEXT=NO_TOKEN_TEXT;this.collectChecks_=collectChecks_;this.checksPreview_=checksPreview_;this.sendChecks_=sendChecks_;this.sendChecks=sendChecks;this.sendChecksAuto_=sendChecksAuto_;this.setAppToken=setAppToken;this.getToken_=getToken_;this.boolOf_=boolOf_;this.colNum_=colNum_;this.checkIdOf_=checkIdOf_;this.checkQtyOf_=checkQtyOf_;this.clean_=clean_;this.fillFromAppAuto=fillFromAppAuto;this.installTimer=installTimer;this.removeTimer=removeTimer;this.fetchBoxes_=fetchBoxes_;this.apply_=apply_;',ctx);

// code = the SM section code the box carries (taskbox.module); tab = the sheet tab (builder.module)
const mk=(tab,label,stage,ids,qty=1,code)=>({module:code||tab,stage,contents:ids.map(i=>({object_id:i,quantity:qty})),builder:{module:tab,label}});
const sheet={M8:{}};
const read=(tab,a1)=>{
  if(!sheet[tab]) return null;
  const m=a1.match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/); if(!m) throw 'bad range '+a1;
  const c1=ctx.colNum_(m[1]), c2=ctx.colNum_(m[3]||m[1]);          // a range may span columns: one array per row, one value per column
  const out=[]; for(let r=+m[2];r<=+(m[4]||m[2]);r++){ const row=[]; for(let c=c1;c<=c2;c++) row.push(sheet[tab][ctx.colShift_('A',c-1)+r]??''); out.push(row); } return out;
};
// the sheet's block titles: row 2 + 25 per section, in the first column of each block (D, K, R)
const titles=(tab,sections)=>{ for(let s=1;s<=sections;s++){ const r=2+25*(s-1);
  sheet[tab]['D'+r]='Box B2 - 21 Objects'; sheet[tab]['K'+r]='Box B3 - 21 Objects'; sheet[tab]['R'+r]='Box A1 - 21 Objects'; } };
const fresh=(sections=2)=>{ sheet.M8={}; titles('M8',sections); };
const ids21=Array.from({length:21},(_,i)=>String(4700+i));
const fail=(m)=>{ throw new Error(m); };

// helpers
if(ctx.colShift_('E',-1)!=='D'||ctx.colShift_('S',-1)!=='R'||ctx.colShift_('AA',-1)!=='Z'||ctx.colShift_('Z',1)!=='AA') fail('colShift_');
if(ctx.sectionOf_('M8','M8')!==1||ctx.sectionOf_('','M8')!==1||ctx.sectionOf_('M8-03','M8')!==3||ctx.sectionOf_('M8-3','M8')!==3) fail('sectionOf_ ok cases');
if(ctx.sectionOf_('M15-04','M8')!==null||ctx.sectionOf_('M8-00','M8')!==null||ctx.sectionOf_('M8-x','M8')!==null) fail('sectionOf_ must refuse a code that is not for this tab');

// Box A1, section 1; a missing tab and an unconfigured stage are skipped
fresh();
let p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21,5),mk('M17','Box A1','A1',ids21),mk('M8','Box B9','B9',ids21)],read);
console.log(ctx.describe_(p));
if(p.writes.length!==1||p.writes[0].qtys[0][0]!==3||p.writes[0].ids[0][0]!=='004700') fail('bad write plan');
if(p.writes[0].idRange!=='S4:S24'||p.writes[0].qtyRange!=='V4:V24') fail('A1 columns');
if(p.skipped.length!==2) fail('skips');

// B2 and B3 land in their own columns, never in A1's
p=ctx.buildPlan_([mk('M8','Box B2','B2',ids21),mk('M8','Box B3','B3',ids21)],read);
if(p.writes.length!==2||p.skipped.length) fail('B2/B3 plan');
const w=Object.fromEntries(p.writes.map(x=>[x.name,x]));
if(w['M8 / Box B2'].idRange!=='E4:E24'||w['M8 / Box B2'].qtyRange!=='H4:H24') fail('B2 columns');
if(w['M8 / Box B3'].idRange!=='L4:L24'||w['M8 / Box B3'].qtyRange!=='O4:O24') fail('B3 columns');

// the section code picks the rows: M8-02 -> rows 29-49, M8-03 -> rows 54-74
fresh(3);
p=ctx.buildPlan_([mk('M8','Box B2','B2',ids21,1,'M8-02'),mk('M8','Box A1','A1',ids21,1,'M8-03')],read);
if(p.writes.length!==2) fail('section plan '+JSON.stringify(p.skipped));
const sw=Object.fromEntries(p.writes.map(x=>[x.name,x]));
if(sw['M8 / Box B2 (section 2)'].idRange!=='E29:E49'||sw['M8 / Box B2 (section 2)'].qtyRange!=='H29:H49') fail('section 2 rows');
if(sw['M8 / Box A1 (section 3)'].idRange!=='S54:S74'||sw['M8 / Box A1 (section 3)'].qtyRange!=='V54:V74') fail('section 3 rows');

// a code for another tab is refused, and a section the sheet does not have is refused (title not there)
fresh(2);
p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21,1,'M15-04'),mk('M8','Box A1','A1',ids21,1,'M8-05')],read);
if(p.writes.length||p.skipped.length!==2) fail('wrong-tab and missing-section must both be skipped');
if(!/không khớp tab/.test(p.skipped[0])||!/không thấy khối/.test(p.skipped[1])) fail('skip reasons '+p.skipped.join('|'));
// the title must name the same box: a Box B3 title above A1's columns blocks the write
fresh(1); sheet.M8['R2']='Box B3 - 21 Objects';
p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21)],read); if(p.writes.length||p.skipped.length!==1) fail('title must match the stage');

// block already filled with the same ids -> same; with something else -> skipped, never overwritten
fresh(1);
ids21.forEach((x,i)=>{ sheet.M8['S'+(4+i)]=('000000'+x).slice(-6); sheet.M8['V'+(4+i)]=2; });
p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21)],read); if(p.same.length!==1||p.writes.length) fail('same');
sheet.M8['S10']='123456'; p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21)],read); if(p.skipped.length!==1||p.writes.length) fail('overwrite guard');
// a single stray cell in the qty column also blocks the write
fresh(1); sheet.M8['V9']=7; p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21)],read); if(p.writes.length) fail('qty guard');
// a filled B2 block is protected, and does not stop B3 from being written
fresh(1); sheet.M8['E10']='123456';
p=ctx.buildPlan_([mk('M8','Box B2','B2',ids21),mk('M8','Box B3','B3',ids21)],read);
if(p.writes.length!==1||p.writes[0].name!=='M8 / Box B3'||p.skipped.length!==1) fail('B2 guard');
// a 12-object A1 (stage size) touches rows 4-15 only; text already sitting in rows 16-24 is neither read nor overwritten
fresh(1); sheet.M8['S20']='keep me'; sheet.M8['V22']=9;
const ids12=ids21.slice(0,12);
p=ctx.buildPlan_([mk('M8','Box A1','A1',ids12)],read);
if(p.writes.length!==1||p.skipped.length) fail('12-object A1 plan '+JSON.stringify(p.skipped));
if(p.writes[0].idRange!=='S4:S15'||p.writes[0].qtyRange!=='V4:V15') fail('12-object A1 ranges '+p.writes[0].idRange);
if(p.writes[0].ids.length!==12||p.writes[0].qtys.length!==12) fail('12 rows written');
// but a stray cell inside the 12 rows still blocks the write
sheet.M8['S10']='123456'; p=ctx.buildPlan_([mk('M8','Box A1','A1',ids12)],read); if(p.writes.length) fail('stray cell inside the block');
// sections that are not 25 rows apart are found by their titles (the real workbook: M8 and M17 are 26 apart, M5 goes 25 then 23)
const lay=(tab,rows)=>{ sheet[tab]={}; rows.forEach(r=>{ sheet[tab]['D'+r]='Box B2 - 21 Objects'; sheet[tab]['K'+r]='Box B3 - 21 Objects'; sheet[tab]['R'+r]='Box A1 - 21 Objects'; }); };
lay('M8',[2,28,54,80]);
p=ctx.buildPlan_([mk('M8','Box B2','B2',ids21,1,'M8-03'),mk('M8','Box A1','A1',ids21,1,'M8-04'),mk('M8','Box B3','B3',ids21,1,'M8-02')],read);
if(p.writes.length!==3||p.skipped.length) fail('26-row spacing '+JSON.stringify(p.skipped));
const w26=Object.fromEntries(p.writes.map(x=>[x.name,x]));
if(w26['M8 / Box B2 (section 3)'].idRange!=='E56:E76'||w26['M8 / Box B2 (section 3)'].qtyRange!=='H56:H76') fail('26-row section 3 '+JSON.stringify(Object.keys(w26)));
if(w26['M8 / Box A1 (section 4)'].idRange!=='S82:S102'||w26['M8 / Box B3 (section 2)'].idRange!=='L30:L50') fail('26-row sections 4 and 2');
lay('M5',[2,27,52,77,102,125,148]);
p=ctx.buildPlan_([mk('M5','Box B2','B2',ids21,1,'M5-06'),mk('M5','Box A1','A1',ids21,1,'M5-07'),mk('M5','Box B3','B3',ids21,1,'M5-08')],read);
const w5=Object.fromEntries(p.writes.map(x=>[x.name,x]));
if(w5['M5 / Box B2 (section 6)']?.idRange!=='E127:E147'||w5['M5 / Box A1 (section 7)']?.idRange!=='S150:S170') fail('25 then 23 spacing '+JSON.stringify(p));
if(p.skipped.length!==1||!/không thấy khối "Box B3" của section 8 \(tab chỉ có 7 khối\)/.test(p.skipped[0])) fail('section 8 does not exist: '+p.skipped.join('|'));
// the 23-row blocks of M5 are tight: 21 rows fit, the next title is right under them
// a note that merely mentions the box in the title column is not a block title
lay('M8',[2]); sheet.M8['D40']='xem Box B2 ở trên'; sheet.M8['D41']='   box b2 - 21 objects';
p=ctx.buildPlan_([mk('M8','Box B2','B2',ids21,1,'M8-02')],read);
if(p.writes.length!==1||p.writes[0].idRange!=='E43:E63') fail('only a title that starts with the box name counts (spaces and case ignored) '+JSON.stringify(p));
// a box that would run over the next block's title is refused, one that fits is written
lay('M8',[2,24]);
p=ctx.buildPlan_([mk('M8','Box B2','B2',ids21,1,'M8-01'),mk('M8','Box B3','B3',ids21.slice(0,20),1,'M8-01')],read);
if(p.writes.length!==1||p.writes[0].name!=='M8 / Box B3'||p.writes[0].idRange!=='L4:L23') fail('overrun guard '+JSON.stringify(p));
if(p.skipped.length!==1||!/không vừa khối này/.test(p.skipped[0])) fail('overrun message '+p.skipped.join('|'));
// two stages on one tab are found independently: a missing B3 title does not hide B2 or A1
lay('M8',[2,27]); delete sheet.M8['K27'];
p=ctx.buildPlan_([mk('M8','Box B2','B2',ids21,1,'M8-02'),mk('M8','Box B3','B3',ids21,1,'M8-02'),mk('M8','Box A1','A1',ids21,1,'M8-02')],read);
if(p.writes.length!==2||p.skipped.length!==1||!/Box B3/.test(p.skipped[0])) fail('independent stages '+JSON.stringify(p));
console.log('apps script plan tests OK');

// ====================================================================== used Object IDs (read-only helper)
const eq=(a,b,m)=>{ if(JSON.stringify(a)!==JSON.stringify(b)) fail(m+': got '+JSON.stringify(a)+' expected '+JSON.stringify(b)); };
const reset=()=>{ Object.keys(sheet).forEach(k=>delete sheet[k]); };
// section s (1-based) starts at row 4+25*(s-1); its block title sits two rows above, one column left of the ID column
const TITLE_COL={B2:'D',B3:'K',A1:'R'}, ID_COL={B2:'E',B3:'L',A1:'S'};
const title=(tab,s,stage,text)=>{ sheet[tab]=sheet[tab]||{}; sheet[tab][TITLE_COL[stage]+(2+25*(s-1))]=text||('Box '+stage+' - 21 Objects'); };
const put=(tab,s,stage,i,v)=>{ sheet[tab]=sheet[tab]||{}; sheet[tab][ID_COL[stage]+(4+25*(s-1)+i)]=v; };

// every write method on a range throws; the fake read only ever hands back getValues()
const boom=(name)=>()=>{ throw new Error('WRITE ATTEMPTED: '+name); };
const guardedRange=(tab,a1)=>{ if(!sheet[tab]) return null;
  return {getValues:()=>read(tab,a1),setValues:boom('setValues'),setNumberFormat:boom('setNumberFormat'),setValue:boom('setValue'),clear:boom('clear'),clearContent:boom('clearContent')}; };
let calls=[];
const safeRead=(tab,a1)=>{ calls.push(tab+'!'+a1); const r=guardedRange(tab,a1); return r?r.getValues():null; };

// ---- the fixture
reset();
// M8: three sections. Section 1 has all three blocks, section 2 only B2 and A1, section 3 B2 and A1 plus a B3 title that must NOT count
['B2','B3','A1'].forEach(st=>title('M8',1,st)); ['B2','A1'].forEach(st=>title('M8',2,st)); ['B2','A1','B3'].forEach(st=>title('M8',3,st));
put('M8',1,'B2',0,'004700');           // plain text
put('M8',1,'B2',1,"'004701");          // apostrophe prefix
put('M8',1,'B2',2,4702);               // a 4-digit number: ignored
put('M8',1,'B2',3,12345);              // 5 digits as a number -> 012345
put('M8',1,'B2',4,'');                 // blank
put('M8',1,'B2',5,'N/A');              // junk
put('M8',1,'B2',6,'  004703 ');        // spaces around
put('M8',1,'B2',7,'1234567');          // 7 digits: ignored
put('M8',1,'B2',8,'12 34');            // not a number: ignored
put('M8',1,'B2',9,'0047x1');           // junk with digits: ignored
put('M8',1,'B2',20,'004720');          // last row of the block (row 24) is read
put('M8',1,'B2',21,'999999');          // row 25 is outside the 21 rows: not read
put('M8',1,'B3',0,'004800'); put('M8',1,'B3',1,'004700');   // 004700 again (duplicate inside the tab)
put('M8',1,'A1',0,'004900');
put('M8',2,'B2',0,'005000'); put('M8',2,'A1',0,'005001');
put('M8',2,'B3',0,'005002');           // no B3 title in section 2: the stage is over, this cell is not an ID of a section
put('M8',3,'B2',0,'005100'); put('M8',3,'A1',0,'005101');
put('M8',3,'B3',0,'005102');           // B3 stopped at section 2, so section 3's B3 is not reached
put('M8',4,'B2',0,'005200');           // no title in section 4 for B2: ignored
title('M8',5,'B2'); put('M8',5,'B2',0,'005300');   // a title after the gap: the walk already stopped
// M17: case-insensitive titles, duplicate of an M8 id, apostrophe, 5 digits
title('M17',1,'B2','BOX B2 - 21 OBJECTS'); title('M17',1,'B3','box b3 - 21 objects');
put('M17',1,'B2',0,'004700'); put('M17',1,'B2',1,'017000'); put('M17',1,'B3',0,"'017001");
// a tab named REF: has titles and ids, but the name filter (not collectUsedIds_) is what keeps it out
['B2','B3','A1'].forEach(st=>title('REF',1,st)); put('REF',1,'B2',0,'777777');
// a module tab with no titles at all: ids sitting there are not in a block
sheet.M3={}; put('M3',1,'B2',0,'123456'); put('M3',1,'A1',0,'654321');
// a title for the wrong stage above a column does not count
sheet.M4={}; title('M4',1,'A1','Box B3 - 21 Objects'); put('M4',1,'A1',0,'424242');
// M5: 70 sections of B2 only, one ID each: only the first 60 are walked
sheet.M5={}; for(let s=1;s<=70;s++){ title('M5',s,'B2'); put('M5',s,'B2',0,String(100000+s)); }

// ---- collectUsedIds_ on several tabs
calls=[];
let r=ctx.collectUsedIds_(['M8','M17','M3','M4','M99'],safeRead);
eq(r.ids,['004700','004701','004703','004720','004800','004900','005000','005001','005100','005101','005102','005300','012345','017000','017001'],'ids sorted, unique, padded (a block title after a gap still counts)');
eq(r.perTab,{M8:13,M17:3,M3:0,M4:0},'perTab counts distinct ids per tab (004700 counted on both tabs, once in ids)');
eq(r.tabsMissing,['M99'],'a tab that does not exist is reported');
['4702','1234567','999999','005002','005200','777777','123456','424242','N/A'].forEach(x=>{
  if(r.ids.some(i=>i.indexOf(x)>=0||i===x)) fail('must not collect '+x); });
if(r.ids.some(i=>!/^\d{6}$/.test(i))) fail('every id is 6 digits');
// read-only proof for the pure function: only the injected read is used, and only for whole columns starting at row 1
if(!calls.length||calls.some(c=>!/^[A-Za-z0-9]+![A-Z]+1:[A-Z]+\d+$/.test(c))) fail('odd read calls '+calls.join(' '));
calls.forEach(c=>{ const m=c.match(/!([A-Z]+)1:([A-Z]+)(\d+)$/); if(m[1]!==m[2]||+m[3]!==ctx.CONFIG.SCAN_ROWS) fail('a read must be one whole column of SCAN_ROWS rows: '+c); });
// per tab and stage: the title column once, the id column once when the stage has a block on the tab
const sc=ctx.CONFIG.SCAN_ROWS, m8=calls.filter(c=>c.indexOf('M8!')===0);
eq(m8,['D','E','K','L','R','S'].map(c=>'M8!'+c+'1:'+c+sc),'M8 reads: 3 title columns and 3 id columns, once each, in stage order');
// a tab with no titles: only the three title columns are read, no ids
eq(calls.filter(c=>c.indexOf('M3!')===0),['D','K','R'].map(c=>'M3!'+c+'1:'+c+sc),'tab without titles reads only its title columns');
// a missing tab costs one read
if(calls.filter(c=>c.indexOf('M99!')===0).length!==1) fail('missing tab must stop at the first read');

// ---- the 60-section cap
calls=[]; r=ctx.collectUsedIds_(['M5'],safeRead);
eq(r.perTab,{M5:60},'cap: 60 sections'); eq(r.ids.length,60,'cap: 60 ids'); eq(r.ids[0],'100001','cap first'); eq(r.ids[59],'100060','cap last');
if(r.ids.indexOf('100061')>=0) fail('section 61 must not be read');
// the 61st and later sections are not read even though their titles are on the tab (the scan reaches section 64)

// ---- edge cases of the pure function
eq(ctx.collectUsedIds_([],safeRead),{ids:[],perTab:{},tabsMissing:[]},'no tabs');
eq(ctx.collectUsedIds_(['M3'],safeRead).ids,[],'tab without titles has no ids');
// a cell holding a Date / boolean / null is not an id and does not break the walk
reset(); title('M1',1,'B2'); put('M1',1,'B2',0,new Date(0)); put('M1',1,'B2',1,true); put('M1',1,'B2',2,null); put('M1',1,'B2',3,'123456');
eq(ctx.collectUsedIds_(['M1'],safeRead).ids,['123456'],'odd cell types');
// usedIdOf_ rules
eq(['004700',"'004700",' 4700 ',"' 12345",12345,'1234','1234567','','N/A',null,undefined,'12.5','-12345'].map(ctx.usedIdOf_),['004700','004700','',"012345",'012345','','','','','','','',''],'usedIdOf_');

// ---- showUsedIds: end to end on a fake spreadsheet where touching any write method throws
const WRITE=/^(set|clear|delete|insert|append|remove|sort|copy|move|merge|protect|activate|trim|apply|create|add|update)/;
const guard=(name,o)=>new Proxy(o,{get(t,k){ if(typeof k==='string'&&WRITE.test(k)) throw new Error('WRITE ATTEMPTED: '+name+'.'+k); return t[k]; }});
reset();
['B2','B3','A1'].forEach(st=>title('M8',1,st)); put('M8',1,'B2',0,'004700'); put('M8',1,'A1',0,"'004900");
title('M17',1,'B2'); put('M17',1,'B2',0,'017000'); put('M17',1,'B2',1,'004700');
['B2','B3','A1'].forEach(st=>title('REF',1,st)); put('REF',1,'B2',0,'777777');
title('Sheet1',1,'B2'); put('Sheet1',1,'B2',0,'888888');
title('m3',1,'B2'); put('m3',1,'B2',0,'999991');
sheet.M2={}; // module tab with no titles
let opened=[], touched=[];
const mkRange=(tab,a1)=>guard('range',{getValues:()=>{ touched.push(tab); return read(tab,a1); }});
const mkSheet=(tab)=>guard('sheet',{getName:()=>tab,getMaxRows:()=>2000,getRange:(a1)=>mkRange(tab,a1)});
const tabsOrder=['M8','REF','M17','M2','Sheet1','m3','Mx','M'];
const fakeSs=guard('spreadsheet',{getSheets:()=>tabsOrder.map(mkSheet),getSheetByName:(n)=>sheet[n]?mkSheet(n):null});
let menu=null;
const fakeUi={showModalDialog:(out,t)=>opened.push({out,title:t}),
  createMenu:(name)=>{ menu={name,items:[],seps:0}; const b={addItem:(l,f)=>{menu.items.push([l,f]);return b;},addSeparator:()=>{menu.seps++;return b;},addToUi:()=>b}; return b; }};
ctx.SpreadsheetApp={getActive:()=>fakeSs,getUi:()=>fakeUi};
ctx.HtmlService={createHtmlOutput:(html)=>{ const o={html,w:0,h:0,setWidth(x){o.w=x;return o;},setHeight(x){o.h=x;return o;}}; return o; }};

eq(ctx.moduleTabNames_(tabsOrder),['M8','M17','M2'],'only names like M8 / M17 are module tabs');
eq(ctx.moduleTabNames_(['M1','M10','M100','REF','M1a','aM1','m1','M','M-1','M 1','']),['M1','M10','M100'],'name filter');
ctx.showUsedIds();
if(opened.length!==1) fail('one dialog expected');
const out=opened[0].out, html=out.html;
if(!/\S/.test(opened[0].title)||out.w<=0||out.h<=0||out.w>600||out.h>500) fail('dialog should be small and have a title');
const area=/<textarea[^>]*>([\s\S]*?)<\/textarea>/.exec(html); if(!area) fail('textarea missing');
eq(area[1].split('\n'),['004700','004900','017000'],'textarea: one id per line');
if(touched.some(t=>t!=='M8'&&t!=='M17'&&t!=='M2')) fail('REF / Sheet1 / m3 must never be read: '+touched.filter((v,i,a)=>a.indexOf(v)===i).join(','));
if(html.indexOf('777777')>=0||html.indexOf('888888')>=0||html.indexOf('999991')>=0) fail('ids from skipped tabs leaked into the dialog');
if(!/Chọn tất cả/.test(html)||!/select\(\)/.test(html)||!/Đã dùng trong bảng tính/.test(html)) fail('button / paste hint missing');
if(!/Tìm thấy 3 ID từ 2 tab \(đã quét 3 tab\)/.test(html)) fail('count line: '+html);
if(/—|–/.test(html)) fail('no em or en dashes in the dialog');
// the menu entry exists and calls showUsedIds; the old entries are still there
ctx.onOpen();
if(!menu.items.some(i=>i[0]==='Lấy ID đã dùng trong bảng'&&i[1]==='showUsedIds')) fail('menu item missing');
if(!menu.items.some(i=>i[1]==='fillFromApp')||!menu.items.some(i=>i[1]==='installTimer')||!menu.items.some(i=>i[1]==='removeTimer')) fail('old menu items must stay');
if(typeof ctx.showUsedIds!=='function') fail('showUsedIds must be a public (no trailing underscore) function');

// no ids at all, and an unreadable tab: still a dialog, no crash
reset(); sheet.M2={};
opened=[]; ctx.showUsedIds();
if(!/Không tìm thấy ID nào/.test(opened[0].out.html)||/<textarea[^>]*>[^<]/.test(opened[0].out.html)) fail('empty case: '+opened[0].out.html);

// ---- HTML escaping: a hostile id (it cannot come out of collectUsedIds_, but if it ever appears it must be inert)
const evil='<img src=x onerror=alert(1)>';
const h=ctx.usedIdsHtml_({ids:[evil,'004700','"><script>alert(2)</script>'],perTab:{'<b>M8</b>':2,'M"17':0},tabsMissing:['<i>M9</i>',"M'1&2"]});
if(h.indexOf('<img')>=0||h.indexOf('<script')>=0||h.indexOf('<b>')>=0||h.indexOf('<i>')>=0) fail('unescaped markup in '+h);
if(h.indexOf('&lt;img src=x onerror=alert(1)&gt;')<0) fail('hostile id should appear as text');
if(h.indexOf('&lt;i&gt;M9&lt;/i&gt;')<0||h.indexOf('M&#39;1&amp;2')<0) fail('tab names must be escaped');
// the only tags are the ones the helper writes itself: div, p, textarea, button
const tags=(h.match(/<\/?[a-zA-Z][^>]*>/g)||[]).map(t=>t.replace(/^<\/?([a-zA-Z]+).*$/,'$1'));
if(tags.some(t=>['div','p','textarea','button'].indexOf(t)<0)) fail('unexpected tag: '+tags.join(','));
eq(ctx.escapeHtml_('<&>"\''),'&lt;&amp;&gt;&quot;&#39;','escapeHtml_');

// ---- readRange_ never asks for rows past the end of the grid (that would throw in Google Sheets)
{ const asked=[]; const mkGrid=(maxRows)=>({getMaxRows:()=>maxRows,getRange:(a1)=>({getValues:()=>{ asked.push(a1); return [[a1]]; }})});
  ctx.SpreadsheetApp={getActive:()=>({getSheetByName:(n)=>n==='T'?mkGrid(249):null})};
  eq(ctx.readRange_('T','D1:D1600'),[['D1:D249']],'clipped to the last row'); eq(ctx.readRange_('T','D1:D100'),[['D1:D100']],'inside the grid: as asked');
  eq(ctx.readRange_('T','D250:D300'),[],'entirely past the end: nothing'); eq(ctx.readRange_('T','E7'),[['E7']],'a single cell is passed through');
  eq(ctx.readRange_('NOPE','D1:D10'),null,'no tab'); eq(asked,['D1:D249','D1:D100','E7'],'only reads inside the grid reached Sheets'); }
// blockStarts_ on its own
reset(); sheet.M1={}; [2,27,52].forEach(r=>{ sheet.M1['D'+r]='Box B2 - 21 Objects'; });
eq(ctx.blockStarts_('M1','B2',read),[4,29,54],'block starts are the rows two under the titles'); eq(ctx.blockStarts_('M1','A1',read),[],'no A1 titles'); eq(ctx.blockStarts_('M99','B2',read),null,'no tab');

// ---- static proof: the read-only helper never even mentions a write method
['collectUsedIds_','usedIdOf_','moduleTabNames_','usedIdsHtml_','escapeHtml_','showUsedIds'].forEach(n=>{
  const src=ctx[n].toString(); const bad=src.match(/\.(set(?!Width|Height)\w*|clear\w*|delete\w*|insert\w*|append\w*|remove\w*|sort\w*Range|copy\w*|merge\w*|activate\w*|protect\w*)\(/g);
  if(bad) fail(n+' calls a write-looking method: '+bad.join(','));
});
console.log('apps script used-ids tests OK');

// ====================================================================== check report: ticks and block contents go to the app, nothing goes into the sheet
{
const mkc=(id,tab,label,stage,list,qty=1,code)=>{ const b=mk(tab,label,stage,list,qty,code); b.builder.box_id=id; return b; };
const itemOf=(res,id)=>{ const it=res.items.find(x=>x.check.box_id===id); if(!it) fail('no check for box '+id+' in '+JSON.stringify(res)); return it; };
const spyRead=(seen)=>(t,a)=>{ seen.push(t+'!'+a); return read(t,a); };

// ---- cell parsers
eq(['003076',3076,' 4702 ',"'004702","' 4702",'','   ',null,undefined,'N/A','1234567','12 34',12.5,true,'0047x1',new Date(0)].map(ctx.checkIdOf_),
   ['003076','003076','004702','004702','004702','','','','',null,null,null,null,null,null,null],'checkIdOf_: blank is "", digits are padded, anything else is null');
eq([5,'5',' 5 ','05',2.5,0,-3,'',' ','abc','1e3',true,null,undefined,'5.0',NaN,1e21,'٣','2 3','0','000','-1'].map(ctx.checkQtyOf_),
   [5,5,5,5,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null,null],'checkQtyOf_: a positive whole number or null');
eq([true,false,'TRUE','FALSE',1,0,'',null,undefined,'x',[true]].map(ctx.boolOf_),[true,false,null,null,null,null,null,null,null,null,null],'boolOf_: only a real boolean counts');
eq(['A','C','E','Z','AA','AZ'].map(ctx.colNum_),[1,3,5,26,27,52],'colNum_');
// the columns of the three stages agree with each other: SC tick, supporters tick (the title column), Object ID, SL, left to right
Object.keys(ctx.CONFIG.STAGES).forEach(st=>{ const c=ctx.CONFIG.STAGES[st];
  eq([c.scCol,c.supCol],[ctx.colShift_(c.idCol,-2),ctx.colShift_(c.idCol,-1)],'columns of '+st); if(!(ctx.colNum_(c.idCol)<ctx.colNum_(c.qtyCol))) fail('SL is right of the Object ID in '+st); });
eq(Object.keys(ctx.CONFIG.STAGES).map(st=>ctx.CONFIG.STAGES[st].scCol+ctx.CONFIG.STAGES[st].supCol),['CD','JK','QR'],'tick columns of B2, B3, A1');
eq(ctx.clean_('bad  token\n tok-1 here','tok-1'),'bad token *** here','clean_ hides the token and folds spaces'); eq(ctx.clean_('x'.repeat(500),'').length,160,'clean_ is short');
if(!(ctx.CONFIG.BLOCK_ROWS<=ctx.CONFIG.CHECK_MAX_ROWS)) fail('a block must fit the 30 rows per box the app takes');

// ---- ticks: a real boolean is read as it is, anything else is unknown (null)
reset(); lay('M8',[2,28]);
Object.assign(sheet.M8,{C2:true,D25:true, J2:false,K25:false, Q2:'TRUE',R25:1, C28:'',D51:0, J28:'x',K51:'', Q28:true});
let res=ctx.collectChecks_([
  mkc(11,'M8','Box B2','B2',[],1,'M8-01'),mkc(12,'M8','Box B3','B3',[],1,'M8-01'),mkc(13,'M8','Box A1','A1',[],1,'M8-01'),
  mkc(14,'M8','Box B2','B2',[],1,'M8-02'),mkc(15,'M8','Box B3','B3',[],1,'M8-02'),mkc(16,'M8','Box A1','A1',[],1,'M8-02')],read);
const tk=(id)=>{ const c=itemOf(res,id).check; return [c.sc_tick,c.supporter_tick]; };
eq(res.skipped,[],'ticks: nothing skipped');
eq(tk(11),[true,true],'B2 section 1: both ticked'); eq(tk(12),[false,false],'B3 section 1: unticked checkboxes read as false');
eq(tk(13),[null,null],'A1 section 1: the text TRUE and the number 1 are not booleans'); eq(tk(14),[null,null],'B2 section 2: a blank and a 0 are not booleans');
eq(tk(15),[null,null],'B3 section 2: text and blank'); eq(tk(16),[true,null],'A1 section 2: SC ticked, the supporters cell is blank');
eq(Object.keys(itemOf(res,11).check),['box_id','sc_tick','supporter_tick','rows'],'the check has exactly the four fields of the contract');

// ---- rows: only the non-blank IDs, ID as 6 digits, SL a positive whole number or null; the 21 data rows and nothing else
reset(); lay('M8',[2,28]);
[[4,'003076',1],[5,4702,'2'],[6,' 005000 ',' 3 '],[7,'',1],[8,"'004800",2.5],[9,'012345',0],[10,'000001',''],[11,'123456','abc'],[12,'999999',true],
 [13,'777777',-1],[14,'654321',null],[15,'  ','7'],[16,'888888','1e3'],[17,'555555',12],[24,'111111',5]].forEach(([r,id,sl])=>{ sheet.M8['E'+r]=id; sheet.M8['H'+r]=sl; });
sheet.M8.E3='Object ID'; sheet.M8.E25='222222'; sheet.M8.E2='333333';          // the sub-header, the row under the block and the title row are not data rows
res=ctx.collectChecks_([mkc(21,'M8','Box B2','B2',['003076',4702,'005000','004800','012345','000001','123456','999999','777777','654321','888888','555555','111111','424242'],1,'M8-01')],read);
eq(itemOf(res,21).check.rows,[{id:'003076',qty:1},{id:'004702',qty:2},{id:'005000',qty:3},{id:'004800',qty:null},{id:'012345',qty:null},{id:'000001',qty:null},
  {id:'123456',qty:null},{id:'999999',qty:null},{id:'777777',qty:null},{id:'654321',qty:null},{id:'888888',qty:null},{id:'555555',qty:12},{id:'111111',qty:5}],'rows and qty parsing');
eq(itemOf(res,21).missing,1,'one of the 14 objects the app lists (424242) is not in the block');
// blanked cells give fewer rows than the app listed; a block with nothing left gives none
reset(); lay('M8',[2,28]); Object.assign(sheet.M8,{E4:'000001',E6:'000003',E9:'000005',H4:1,H6:1,H9:2});
res=ctx.collectChecks_([mkc(22,'M8','Box B2','B2',['000001','000002','000003','000004','000005'],1,'M8-01'),mkc(23,'M8','Box B3','B3',['000009'],1,'M8-01')],read);
eq(itemOf(res,22).check.rows.map(r=>r.id),['000001','000003','000005'],'the blank cells are gone from the rows'); eq(itemOf(res,22).missing,2,'two objects missing');
eq(itemOf(res,23).check.rows,[],'an empty block gives no rows'); eq(itemOf(res,23).missing,1,'and everything is missing');
// an ID cell that holds text is not guessed: the box is held back and the cell is named
reset(); lay('M8',[2]); Object.assign(sheet.M8,{L4:'003076',L5:'hết hàng',L6:'004702'});
res=ctx.collectChecks_([mkc(24,'M8','Box B3','B3',['003076','004702'],1,'M8-01')],read);
eq(res.items,[],'an odd ID cell holds the box back'); eq(res.skipped,['M8 / Box B3: ô L5 có "hết hàng", không phải ID: sửa hoặc xoá ô đó, chưa gửi'],'the odd cell is named');

// ---- sections the app cannot place are listed and not sent
reset(); lay('M8',[2,28]);
const none=ctx.collectChecks_([
  mk('M8','Box B2','B2',ids21),                                                              // the feed gave no box id
  mkc('7','M8','Box B2','B2',ids21),mkc(0,'M8','Box B2','B2',ids21),mkc(1.5,'M8','Box B2','B2',ids21),   // not whole positive numbers
  mkc(61,'M8','Box B2','B2',ids21,1,'M8-05'),mkc(62,'M99','Box B2','B2',ids21),mkc(63,'M8','Box B2','B2',ids21,1,'M15-04'),mkc(64,'M8','Box B9','B9',ids21)],read);
eq(none.items,[],'none of them is sent');
eq(none.skipped,['M8 / Box B2: Box builder không gửi mã hộp','M8 / Box B2: Box builder không gửi mã hộp','M8 / Box B2: Box builder không gửi mã hộp','M8 / Box B2: Box builder không gửi mã hộp',
  'M8 / Box B2 (section 5): không thấy trong bảng (khối "Box B2" của section 5, tab chỉ có 2 khối)','M99 / Box B2: không thấy trong bảng (không có tab "M99")',
  'M8 / Box B2: không thấy trong bảng (mã section "M15-04" không khớp tab M8)','M8 / Box B9: chưa cấu hình cột cho B9'],'skip reasons');

// ---- M5 spacing (25, then 23): the same blocks the fill finds; the supporters' tick that would sit on the next title is not read, not guessed
reset(); lay('M5',[2,27,52,77,102,125,148]);
p=ctx.buildPlan_([mk('M5','Box B2','B2',ids21,1,'M5-05'),mk('M5','Box B2','B2',ids21,1,'M5-06'),mk('M5','Box B2','B2',ids21,1,'M5-07')],read);
eq(p.writes.map(x=>x.idRange),['E104:E124','E127:E147','E150:E170'],'where the fill writes on M5');
Object.assign(sheet.M5,{C77:true,D100:true, C102:false,E104:'000104',E124:'000124',E125:'999999', C125:true,E127:'000127',E147:'000147',E148:'999998', C148:true,E150:'000150',D171:true});
const seen=[];
res=ctx.collectChecks_([mkc(31,'M5','Box B2','B2',[],1,'M5-04'),mkc(32,'M5','Box B2','B2',[],1,'M5-05'),mkc(33,'M5','Box B2','B2',[],1,'M5-06'),
  mkc(34,'M5','Box B2','B2',[],1,'M5-07'),mkc(35,'M5','Box B2','B2',[],1,'M5-08')],spyRead(seen));
eq(tk(31),[true,true],'M5 section 4: 25 rows to the next title, the tick is read'); eq(tk(32),[false,null],'M5 section 5: the next title is on the supporters row');
eq(tk(33),[true,null],'M5 section 6: same'); eq(tk(34),[true,true],'M5 section 7: last block, the tab goes on');
eq(itemOf(res,32).check.rows,[{id:'000104',qty:null},{id:'000124',qty:null}],'section 5 rows stop above the next title'); eq(itemOf(res,33).check.rows,[{id:'000127',qty:null},{id:'000147',qty:null}],'section 6 rows');
eq(itemOf(res,34).check.rows,[{id:'000150',qty:null}],'section 7 rows'); eq(res.skipped,['M5 / Box B2 (section 8): không thấy trong bảng (khối "Box B2" của section 8, tab chỉ có 7 khối)'],'no section 8');
eq(seen,['M5!D1:D1600','M5!C77:H100','M5!C102:H124','M5!C125:H147','M5!C148:H171'],
  'one lookup of the titles, then one read per box; the block above a next title stops above it (rows 124 and 147), so its title cell is never read');
// the next title on, above or below the supporters' row (title + 23): only below it is safe
[[24,null,['000023']],[25,null,['000023','111111']],[26,true,['000023','111111']]].forEach(([next,want,ids])=>{
  reset(); lay('M8',[2,next]); Object.assign(sheet.M8,{E23:'000023',E24:'111111',E25:'222222'}); if(next!==25) sheet.M8.D25=true;
  const sn=[]; const r1=ctx.collectChecks_([mkc(41,'M8','Box B2','B2',[],1,'M8-01')],spyRead(sn));
  eq(r1.items[0].check.supporter_tick,want,'next title at row '+next); eq(r1.items[0].check.rows.map(x=>x.id),ids,'rows stop above the next title at row '+next);
  eq(sn[1],want===null?'M8!C2:H'+Math.min(24,next-1):'M8!C2:H25','the block is read down to the supporters row only when it is free');
});

// ====================================================================== the environment: a recording spreadsheet, a network, properties, a menu
const log=[], writes=[], logs=[], net=[], props={}, alerts=[], prompts=[];
const wrap=(label,o,sink)=>new Proxy(o,{get(t,k,recv){
  if(typeof k!=='string') return t[k];
  if(!(k in t)){ (sink||log).push(label+'.'+k+' (not allowed)'); return ()=>{ throw new Error('UNEXPECTED CALL '+label+'.'+k); }; }
  const v=t[k]; if(typeof v!=='function') return v;
  return (...a)=>{ (sink||log).push(label+'.'+k); const out=v.apply(t,a); return out===t?recv:out; };
}});
let maxRows={}, canWrite=false, reply=()=>({code:200,body:'{"boxes":[]}'}), answerYes=true, promptAnswer={button:'OK',text:''};
const checkRange=(tab,a1)=>{ const m=/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(a1); const mx=maxRows[tab]||2000; if(m&&(+m[4]>mx||+m[2]>mx)) throw new Error('range outside the tab: '+tab+'!'+a1); };
const fakeBook=wrap('Spreadsheet',{getSheetByName:(n)=>!sheet[n]?null:wrap('Sheet',{
  getMaxRows:()=>maxRows[n]||2000,
  getRange:(a1)=>{ checkRange(n,a1); const rg={getValues:()=>read(n,a1)};
    if(canWrite){ rg.setNumberFormat=()=>rg; rg.setValues=(v)=>{ const m=/^([A-Z]+)(\d+):/.exec(a1); writes.push(a1); v.forEach((row,i)=>{ sheet[n][m[1]+(+m[2]+i)]=row[0]; }); return rg; }; }
    return wrap('Range',rg); }})});
const fakeUi2={ButtonSet:{OK:'OK',YES_NO:'YES_NO',OK_CANCEL:'OK_CANCEL'},Button:{YES:'YES',NO:'NO',OK:'OK',CANCEL:'CANCEL'},
  alert:(t,m,b)=>{ alerts.push(m===undefined?{title:'',text:t,buttons:'OK'}:{title:t,text:m,buttons:b}); return b==='YES_NO'?(answerYes?'YES':'NO'):'OK'; },
  prompt:(t,m,b)=>{ prompts.push({title:t,text:m}); return {getSelectedButton:()=>promptAnswer.button,getResponseText:()=>promptAnswer.text}; },
  createMenu:(name)=>{ menu={name,items:[],seps:0}; const b={addItem:(l,f)=>{menu.items.push([l,f]);return b;},addSeparator:()=>{menu.seps++;return b;},addToUi:()=>b}; return b; }};
ctx.SpreadsheetApp={getActive:()=>fakeBook,getUi:()=>fakeUi2};
ctx.UrlFetchApp={fetch:(url,opts)=>{ opts=opts||{}; log.push('NET '+(opts.method||'get')); net.push({url,opts}); const r=reply(url,opts,net.length);
  if(r.throws) throw new Error(r.throws); return {getResponseCode:()=>r.code,getContentText:()=>r.body}; }};
ctx.PropertiesService={getScriptProperties:()=>wrap('Properties',{getProperty:(k)=>(k in props?props[k]:null),setProperty:(k,v)=>{ props[k]=v; }},[])};
const realConsole=ctx.console; ctx.console={log:(m)=>logs.push(String(m))};
const clear=()=>{ [log,writes,logs,net,alerts,prompts].forEach(a=>a.length=0); };
const READ_ONLY=/^(Spreadsheet\.getSheetByName|Sheet\.getMaxRows|Sheet\.getRange|Range\.getValues|NET (get|post))$/;
const onlyReads=(why)=>{ const bad=log.filter(x=>!READ_ONLY.test(x)); if(bad.length) fail(why+': a call that is not a plain read reached the sheet: '+bad.join(', ')); };
const TOKEN='tok-test-123';                                         // made up
const feedReply=(boxes,post)=>(url,opts)=>/\/api\/sheet-boxes$/.test(url)?{code:200,body:JSON.stringify({boxes})}:post(url,opts);
const ok200=()=>({code:200,body:'{"ok":true,"saved":3,"skipped":1}'});
const mainFixture=()=>{
  reset(); lay('M8',[2,28]); lay('M17',[2,28]);
  Object.assign(sheet.M8,{C2:true,D25:true,E4:'003076',E6:'005000',H4:1,H6:2, J2:true,K25:false,L4:'001111',O4:1, S30:'002001',S31:'002002',V30:1,V31:3});
  Object.assign(sheet.M17,{C2:false,D25:true,E4:'002222',H4:1});
  return [mkc(21,'M8','Box B2','B2',['003076','004702','005000'],1,'M8-01'),mkc(22,'M8','Box B3','B3',['001111'],1,'M8-01'),
    mkc(23,'M8','Box A1','A1',['002001','002002'],1,'M8-02'),mkc(24,'M17','Box B2','B2',['002222'],1,'M17-01'),mkc(25,'M8','Box B2','B2',['009999'],1,'M8-05')];
};
const PREVIEW=['Sẽ gửi kết quả kiểm của 4 hộp (M8: 3, M17: 1).','SC đã tick "cần": 2 hộp.','Người hỗ trợ đã tick: 2 hộp.','Hộp thiếu món (ô ID bị bỏ trống): 1 hộp.',
  'Không đọc được ô tick (gửi là chưa biết): SC 1 hộp, người hỗ trợ 1 hộp.','KHÔNG GỬI M8 / Box B2 (section 5): không thấy trong bảng (khối "Box B2" của section 5, tab chỉ có 2 khối)'].join('\n');
const PAYLOAD='{"checks":[{"box_id":21,"sc_tick":true,"supporter_tick":true,"rows":[{"id":"003076","qty":1},{"id":"005000","qty":2}]},'+
  '{"box_id":22,"sc_tick":true,"supporter_tick":false,"rows":[{"id":"001111","qty":1}]},'+
  '{"box_id":23,"sc_tick":null,"supporter_tick":null,"rows":[{"id":"002001","qty":1},{"id":"002002","qty":3}]},'+
  '{"box_id":24,"sc_tick":false,"supporter_tick":true,"rows":[{"id":"002222","qty":1}]}]}';

// ---- a tab that ends before the supporters row (the real readRange_ on a fake grid that refuses ranges outside it)
reset(); lay('M3',[2]); Object.assign(sheet.M3,{C2:true,D25:true,E4:'000004',E24:'000024'});
const edge=(rows)=>{ maxRows={M3:rows}; clear(); return ctx.collectChecks_([mkc(51,'M3','Box B2','B2',[],1,'M3-01')]).items[0].check; };
eq(edge(24),{box_id:51,sc_tick:true,supporter_tick:null,rows:[{id:'000004',qty:null},{id:'000024',qty:null}]},'the tab ends on the last data row: the supporters row is outside, unknown');
eq(edge(25).supporter_tick,true,'the tab goes one row further: the tick is read'); eq(edge(10),{box_id:51,sc_tick:true,supporter_tick:null,rows:[{id:'000004',qty:null}]},'a tab that ends inside the block gives the rows it has');
eq(edge(3),{box_id:51,sc_tick:true,supporter_tick:null,rows:[]},'a tab that ends before the data: no rows');
maxRows={}; onlyReads('tab edge');

// ---- the preview text, and that building it only reads
let feed=mainFixture(); clear(); maxRows={};
res=ctx.collectChecks_(feed); onlyReads('collectChecks_');
eq(log.filter(x=>x==='Range.getValues').length,8,'reads: the titles once per tab and stage (4), then one read per box found (4)');
eq(ctx.checksPreview_(res),PREVIEW,'preview text'); if(/—|–/.test(PREVIEW)) fail('no em or en dashes');
eq(res.items.map(i=>[i.check.box_id,i.name,i.missing]),[[21,'M8 / Box B2',1],[22,'M8 / Box B3',0],[23,'M8 / Box A1 (section 2)',0],[24,'M17 / Box B2',0]],'what is sent, and what is missing');
eq(ctx.checksPreview_({items:[],skipped:[]}),'Chưa có box nào ở trạng thái Đã duyệt.','nothing approved'); eq(ctx.checksPreview_({items:[],skipped:['M8 / Box B2: x']}),'KHÔNG GỬI M8 / Box B2: x','only skipped boxes');
eq(ctx.checksPreview_({items:[{tab:'M8',missing:0,check:{sc_tick:true,supporter_tick:true,rows:[]}}],skipped:[]}).split('\n'),
  ['Sẽ gửi kết quả kiểm của 1 hộp (M8: 1).','SC đã tick "cần": 1 hộp.','Người hỗ trợ đã tick: 1 hộp.','Hộp thiếu món (ô ID bị bỏ trống): 0 hộp.','Khối còn trống hoàn toàn: 1 hộp.'],'an empty block is said so');

// ---- the menu: the new items are there, the old ones stay
ctx.onOpen();
eq(menu.items.filter(i=>/kết quả kiểm|mã Box/.test(i[0])),[['Gửi kết quả kiểm cho Box builder (xem trước)','sendChecks'],['Đặt mã Box builder','setAppToken']],'new menu items');
['fillFromApp','installTimer','removeTimer','showUsedIds'].forEach(f=>{ if(!menu.items.some(i=>i[1]===f)) fail('menu item for '+f+' must stay'); });
if(menu.items.some(i=>!/^[A-Za-z]+$/.test(i[1])||typeof ctx[i[1]]!=='function')) fail('every menu handler is a public function');

// ---- menu send: no token, then preview + NO, then preview + YES: exactly one GET and one POST, exactly the contract
clear(); delete props.SHEET_TOKEN; reply=feedReply(feed,ok200);
ctx.sendChecks();
eq(alerts.map(a=>a.text),[ctx.NO_TOKEN_TEXT],'without a code the person is told how to set it'); eq(net.length,0,'and nothing is fetched or sent');
props.SHEET_TOKEN=TOKEN; clear(); answerYes=false;
ctx.sendChecks();
eq(alerts.length,1,'one question'); eq(alerts[0].title,'Box builder: sẽ gửi kết quả kiểm','question title'); eq(alerts[0].buttons,'YES_NO','a yes/no question');
eq(alerts[0].text,PREVIEW+'\n\nChỉ gửi cho Box builder, không ghi gì vào bảng tính. Tiếp tục?','the question shows the preview'); eq(net.map(n=>n.opts.method||'get'),['get'],'answering no sends nothing');
onlyReads('preview');
clear(); answerYes=true;
ctx.sendChecks(); onlyReads('send');
eq(net.map(n=>(n.opts.method||'get')+' '+n.url.replace(ctx.CONFIG.APP_URL,'')),['get /api/sheet-boxes','post /api/sheet/checks'],'one read of the feed, one report');
const post=net[1].opts;
eq(post.payload,PAYLOAD,'the body is exactly the contract'); eq(JSON.parse(post.payload).checks.length,4,'four boxes');
eq(post.headers,{'X-Sheet-Token':TOKEN},'only the token header'); eq(post.contentType,'application/json','json'); eq(post.method,'post','post'); eq(post.muteHttpExceptions,true,'errors come back as codes');
if(net[0].url!==ctx.CONFIG.APP_URL+'/api/sheet-boxes'||('X-Sheet-Token' in (net[0].opts.headers||{}))) fail('the feed read must not carry the token');
eq(alerts.map(a=>a.text).slice(-1),['Đã gửi 4 hộp: Box builder ghi nhận 3, bỏ qua 1 (hộp đã xoá, chưa duyệt hoặc đã lên SM).'],'result message');
// a feed with nothing sendable: the person is told, nothing is posted
clear(); reply=feedReply([mk('M8','Box B2','B2',ids21)],ok200); ctx.sendChecks();
eq(net.map(n=>n.opts.method||'get'),['get'],'nothing to send, no POST'); eq(alerts.map(a=>a.text),['KHÔNG GỬI M8 / Box B2: Box builder không gửi mã hộp'],'and the reason is shown');
clear(); reply=feedReply([],ok200); ctx.sendChecks(); eq(alerts.map(a=>a.text),['Chưa có box nào ở trạng thái Đã duyệt.'],'empty feed'); eq(net.length,1,'only the read');
// the app cannot be reached for the feed: the person reads a message, not a stack
clear(); reply=()=>({code:500,body:''}); ctx.sendChecks(); if(!/Không tải được Box builder \(HTTP 500\)/.test(alerts[0].text)||net.length!==1) fail('feed error: '+JSON.stringify(alerts));

// ---- answers of the app: 401, 503, other codes, a lost connection, a body that is not ours, several requests
const one=[{box_id:5,sc_tick:true,supporter_tick:false,rows:[{id:'000001',qty:1}]}];
const send=(r,list)=>{ clear(); reply=typeof r==='function'?r:()=>r; return ctx.sendChecks_(list||one); };
let out=send({code:401,body:'{"detail":"x"}'});
eq([out.ok,net.length],[false,1],'401 is one request'); if(!/Mã Box builder sai \(HTTP 401\)/.test(out.text)||!/Đặt mã Box builder/.test(out.text)) fail('401 text: '+out.text);
out=send({code:503,body:'<html>Service Unavailable</html>'}); if(out.ok||!/HTTP 503/.test(out.text)||!/chưa đặt mã/.test(out.text)||/<html/.test(out.text)) fail('503 text: '+out.text);
out=send({code:500,body:'<html><body>Oops</body></html>'}); if(out.ok||!/HTTP 500/.test(out.text)||/<|Oops/.test(out.text)) fail('500 text must not echo a page: '+out.text);
out=send({code:422,body:'{"detail":"Quá nhiều hộp"}'}); if(out.ok||!/HTTP 422: Quá nhiều hộp/.test(out.text)) fail('422 text: '+out.text);
out=send({code:422,body:JSON.stringify({detail:'bad '+TOKEN+' '+'x'.repeat(400)})}); if(out.text.indexOf(TOKEN)>=0||out.text.length>260) fail('an echoed token or a long detail is cut: '+out.text);
out=send({code:422,body:'{"detail":[{"msg":"x"}]}'}); if(!/HTTP 422\)/.test(out.text)) fail('a detail that is not a string is dropped: '+out.text);
out=send({throws:'DNS error for '+TOKEN}); if(out.ok||!/Không kết nối được Box builder/.test(out.text)||out.text.indexOf(TOKEN)>=0) fail('lost connection: '+out.text);
out=send({code:200,body:'<html>login</html>'}); if(out.ok||!/không đúng dạng/.test(out.text)) fail('200 with a page: '+out.text);
out=send({code:200,body:'{"ok":false}'}); if(out.ok) fail('ok:false is not success');
out=send({code:302,body:''}); if(out.ok||!/HTTP 302/.test(out.text)||net[0].opts.followRedirects!==false) fail('a redirect is an error, and is not followed: '+out.text);
out=send({code:200,body:'{"ok":true,"saved":1,"skipped":0}'}); eq([out.ok,out.saved,out.skipped,out.text],[true,1,0,'Đã gửi 1 hộp: Box builder ghi nhận 1, bỏ qua 0.'],'success');
out=send({code:200,body:'{"ok":true}'}); eq([out.ok,out.saved,out.skipped],[true,0,0],'a reply without counts counts zero');
out=send(ok200,[]); eq([out.ok,net.length,out.text],[true,0,'Không có hộp nào để gửi.'],'an empty list posts nothing');
// more than 200 boxes go in requests of at most 200, in order; a failure stops the rest and says how far it got
const many=Array.from({length:450},(_,i)=>({box_id:i+1,sc_tick:null,supporter_tick:null,rows:[]}));
out=send(ok200,many); eq(net.map(n=>JSON.parse(n.opts.payload).checks.length),[200,200,50],'450 boxes: 200, 200, 50');
eq(net.map(n=>JSON.parse(n.opts.payload).checks[0].box_id),[1,201,401],'in order'); eq([out.ok,out.saved,out.skipped],[true,9,3],'counts add up');
out=send((u,o,n)=>n===2?{code:401,body:'{}'}:ok200(),many); eq(net.length,2,'the third request is not made after a 401'); if(out.ok||!/^Đã gửi 200 hộp trước đó\. Mã Box builder sai/.test(out.text)) fail('partial failure text: '+out.text);
// the code goes only over https
const realUrl=ctx.CONFIG.APP_URL; ctx.CONFIG.APP_URL='http://app.example'; out=send(ok200); ctx.CONFIG.APP_URL=realUrl;
if(out.ok||net.length||!/https:\/\//.test(out.text)) fail('the code must not go over plain http: '+out.text);

// ---- the code: typed once, kept in Script Properties, read back for the POST only; never in CONFIG or the file
Object.keys(props).forEach(k=>delete props[k]);
if(JSON.stringify(ctx.CONFIG).indexOf('tok-')>=0||Object.keys(ctx.CONFIG).some(k=>/token/i.test(k)&&k!=='TOKEN_PROPERTY')) fail('CONFIG must hold no code');
eq(ctx.CONFIG.TOKEN_PROPERTY,'SHEET_TOKEN','only the name of the property is in CONFIG');
out=send(ok200); eq([out.ok,out.text,net.length],[false,ctx.NO_TOKEN_TEXT,0],'no property, no request');
const realCode=ctx.CONFIG.ACCESS_CODE; ctx.CONFIG.ACCESS_CODE='shared-code-xyz'; out=send(ok200); eq(net.length,0,'the old access code is not a token'); 
props.SHEET_TOKEN='tok-first'; send(ok200); eq(net[0].opts.headers,{'X-Sheet-Token':'tok-first'},'the header carries the property, not the access code');
if(JSON.stringify(net[0].opts).indexOf('shared-code-xyz')>=0) fail('the access code must not be sent to the report route'); ctx.CONFIG.ACCESS_CODE=realCode;
props.SHEET_TOKEN='  tok-second \n'; send(ok200); eq(net[0].opts.headers,{'X-Sheet-Token':'tok-second'},'read again on every send, trimmed');
// setting it
const setIt=(button,text)=>{ clear(); promptAnswer={button,text}; ctx.setAppToken(); };
setIt('OK','  tok-new-456  '); eq(props.SHEET_TOKEN,'tok-new-456','stored trimmed, under the configured name'); eq(alerts.map(a=>a.text),['Đã lưu mã.'],'confirmed');
if(alerts.concat(prompts).some(a=>JSON.stringify(a).indexOf('tok-new-456')>=0)) fail('the code is never shown back'); eq(prompts.length,1,'asked once');
setIt('CANCEL','tok-cancel'); eq(props.SHEET_TOKEN,'tok-new-456','cancel keeps the old code'); eq(alerts.length,0,'cancel is silent');
setIt('OK','   '); eq(props.SHEET_TOKEN,'tok-new-456','an empty answer keeps the old code'); eq(alerts.map(a=>a.text),['Chưa nhập mã, giữ nguyên mã cũ.'],'and says so');
['has space','two\nlines','mã-việt','x'.repeat(201)].forEach(bad=>{ setIt('OK',bad); eq(props.SHEET_TOKEN,'tok-new-456','refused: '+JSON.stringify(bad.slice(0,12))); if(!/Chưa lưu/.test(alerts[0].text)) fail('refusal text'); });
setIt('OK','x'.repeat(200)); eq(props.SHEET_TOKEN.length,200,'200 characters is fine');
props.SHEET_TOKEN=TOKEN;

// ---- the timer: the fill first, then the report; a failed or impossible report never breaks it
const timerFixture=()=>{ reset(); lay('M8',[2,28]); sheet.M8.C2=false; sheet.M8.D25=false; canWrite=true;
  feed=[mkc(71,'M8','Box B2','B2',['003076','004702'],1,'M8-01')]; reply=feedReply(feed,ok200); clear(); };
timerFixture(); ctx.fillFromAppAuto();
eq(writes,['E4:E5','H4:H5'],'the fill wrote its own block and nothing else');
eq(log.filter(x=>/^Range\.set/.test(x)),['Range.setNumberFormat','Range.setValues','Range.setValues'],'one block: one format, two writes');
eq(log.filter(x=>/^NET/.test(x)),['NET get','NET post'],'one read of the feed, one report'); if(log.lastIndexOf('Range.setValues')>log.indexOf('NET post')) fail('the report goes after the fill');
eq(JSON.parse(net[1].opts.payload),{checks:[{box_id:71,sc_tick:false,supporter_tick:false,rows:[{id:'003076',qty:1},{id:'004702',qty:1}]}]},'the report shows what the fill just wrote');
eq(logs.slice(-1)[0],'Đã gửi 1 hộp: Box builder ghi nhận 3, bỏ qua 1 (hộp đã xoá, chưa duyệt hoặc đã lên SM).','the outcome is logged');
canWrite=false;
// no code set: the fill still runs, nothing is posted, no error
delete props.SHEET_TOKEN; timerFixture(); ctx.fillFromAppAuto();
eq(writes,['E4:E5','H4:H5'],'fill still runs without a code'); eq(net.map(n=>n.opts.method||'get'),['get'],'no report without a code'); eq(logs.slice(-1)[0],'Chưa đặt mã Box builder: không gửi kết quả kiểm.','and the log says why');
// the app says 401, 503 or is gone: logged, not thrown, the fill is already done
props.SHEET_TOKEN=TOKEN;
[[{code:401,body:'{}'},/Mã Box builder sai/],[{code:503,body:''},/HTTP 503/],[{throws:'timeout'},/Không kết nối được/]].forEach(([bad,rx])=>{
  timerFixture(); reply=feedReply(feed,()=>bad); ctx.fillFromAppAuto(); eq(writes,['E4:E5','H4:H5'],'fill done before the report failed');
  if(!rx.test(logs.slice(-1)[0])) fail('the failure is logged: '+logs.join(' | '));
});
// a reading problem inside the report is caught as well (here: the tab vanishes between the fill and the read)
timerFixture(); ctx.SpreadsheetApp.getActive=()=>{ throw new Error('boom '+TOKEN); }; ctx.sendChecksAuto_(feed);
if(!/^Không gửi được kết quả kiểm: boom \*\*\*$/.test(logs.slice(-1)[0])) fail('caught and scrubbed: '+logs.join('|'));
ctx.SpreadsheetApp.getActive=()=>fakeBook; canWrite=false;
// the feed cannot be read: the fill fails exactly as before and nothing is posted
timerFixture(); reply=()=>({code:500,body:''}); let threw=false; try{ ctx.fillFromAppAuto(); }catch(e){ threw=/Không tải được Box builder/.test(e.message); }
if(!threw||net.length!==1||writes.length) fail('a failed read must stop the run before any write or report');
canWrite=false;
// the timer entries are unchanged: the trigger is still the fill handler, removeTimer touches only it
{ const deleted=[]; let made=null; const triggers=[{id:1,getHandlerFunction:()=>'fillFromAppAuto'},{id:2,getHandlerFunction:()=>'somethingElse'},{id:3,getHandlerFunction:()=>'fillFromAppAuto'}];
  ctx.ScriptApp={getProjectTriggers:()=>triggers,deleteTrigger:(t)=>deleted.push(t.id),newTrigger:(fn)=>({timeBased:()=>({everyMinutes:(n)=>({create:()=>{ made=[fn,n]; }})})})};
  ctx.removeTimer(); eq(deleted,[1,3],'removeTimer deletes only the fill trigger'); deleted.length=0; clear(); ctx.installTimer();
  eq(made,['fillFromAppAuto',30],'the trigger is the same handler every 30 minutes'); eq(deleted,[1,3],'installTimer replaces, never doubles');
  if(!/kết quả kiểm/.test(alerts[0].text)||!/không bao giờ ghi đè/.test(alerts[0].text)) fail('the timer alert says both things: '+alerts[0].text); }

// ---- static proofs on the file itself
const src=fs.readFileSync('integrations/google_sheets_apps_script.gs','utf8');
const beforeApply=src.slice(0,src.indexOf('function apply_('));
const bad=beforeApply.match(/\.(set(?!Width|Height|Property)\w*|clear\w*|delete(?!Trigger)\w*|insert\w*|append\w*|remove\w*|sort\w*Range|copy\w*|merge\w*|activate\w*|protect\w*)\(/g);
if(bad) fail('only apply_ may call a write method on the sheet, found: '+bad.join(','));
if((src.match(/\.setProperty\(/g)||[]).length!==1||src.indexOf('.setProperty(')<src.indexOf('function setAppToken(')) fail('the only setProperty is in setAppToken');
['collectChecks_','boolOf_','colNum_','checkIdOf_','checkQtyOf_','checksPreview_','getToken_','clean_','sendChecks_','sendChecks','sendChecksAuto_','readRange_','blockStarts_','sectionOf_'].forEach(n=>{
  const f=ctx[n].toString(); const b=f.match(/\.(set\w*|clear\w*|delete\w*|insert\w*|append\w*|remove\w*|copy\w*|merge\w*|activate\w*|protect\w*)\(/g);
  if(b) fail(n+' calls a write-looking method: '+b.join(',')); });
if((src.match(/UrlFetchApp\.fetch\(/g)||[]).length!==2||(src.match(/CONFIG\.APP_URL \+ '/g)||[]).length!==2) fail('the script talks to the app in exactly two places');
if(!/CONFIG\.APP_URL \+ '\/api\/sheet-boxes'/.test(src)||!/CONFIG\.APP_URL \+ '\/api\/sheet\/checks'/.test(src)) fail('the two routes');
if(!/headers: \{ 'X-Sheet-Token': token \}/.test(src)||/X-Sheet-Token'\s*:\s*'/.test(src)) fail('the code is a variable read from Script Properties, never a literal');
if(/docs\.google\.com|spreadsheets\/d\//.test(src)) fail('no sheet link in the file');
const header=src.slice(0,src.indexOf('const CONFIG'));
if(!/\/api\/sheet\/checks/.test(header)||!/X-Sheet-Token/.test(header)||!/ONLY into cells that are completely empty/.test(header)||!/call no write method on the sheet/.test(header)) fail('the header must say what it sends and what it never does');
if(/It only READS from the app/.test(header)) fail('the old "only reads from the app" rule is no longer true');
ctx.console=realConsole;
console.log('apps script check-report tests OK');
}
