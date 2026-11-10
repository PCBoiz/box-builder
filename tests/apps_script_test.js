const fs=require('fs'); const vm=require('vm');
const ctx={console,String,Number,Math,Object,JSON,parseInt,RegExp}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync('integrations/google_sheets_apps_script.gs','utf8')+'\nthis.buildPlan_=buildPlan_;this.describe_=describe_;this.CONFIG=CONFIG;this.sectionOf_=sectionOf_;this.colShift_=colShift_;this.collectUsedIds_=collectUsedIds_;this.usedIdOf_=usedIdOf_;this.moduleTabNames_=moduleTabNames_;this.usedIdsHtml_=usedIdsHtml_;this.escapeHtml_=escapeHtml_;this.showUsedIds=showUsedIds;this.onOpen=onOpen;',ctx);

// code = the SM section code the box carries (taskbox.module); tab = the sheet tab (builder.module)
const mk=(tab,label,stage,ids,qty=1,code)=>({module:code||tab,stage,contents:ids.map(i=>({object_id:i,quantity:qty})),builder:{module:tab,label}});
const sheet={M8:{}};
const read=(tab,a1)=>{
  if(!sheet[tab]) return null;
  const m=a1.match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/); if(!m) throw 'bad range '+a1;
  const out=[]; for(let r=+m[2];r<=+(m[4]||m[2]);r++) out.push([sheet[tab][m[1]+r]??'']); return out;
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
eq(r.ids,['004700','004701','004703','004720','004800','004900','005000','005001','005100','005101','012345','017000','017001'],'ids sorted, unique, padded');
eq(r.perTab,{M8:11,M17:3,M3:0,M4:0},'perTab counts distinct ids per tab (004700 counted on both tabs, once in ids)');
eq(r.tabsMissing,['M99'],'a tab that does not exist is reported');
['4702','1234567','999999','005002','005102','005200','005300','777777','123456','424242','N/A'].forEach(x=>{
  if(r.ids.some(i=>i.indexOf(x)>=0||i===x)) fail('must not collect '+x); });
if(r.ids.some(i=>!/^\d{6}$/.test(i))) fail('every id is 6 digits');
// read-only proof for the pure function: only the injected read is used, and only for single cells or one 21-row column
if(!calls.length||calls.some(c=>!/^[A-Za-z0-9]+![A-Z]+\d+(:[A-Z]+\d+)?$/.test(c))) fail('odd read calls '+calls.join(' '));
calls.filter(c=>c.indexOf(':')>0).forEach(c=>{ const m=c.match(/!([A-Z]+)(\d+):([A-Z]+)(\d+)$/); if(m[1]!==m[3]||(+m[4])-(+m[2])!==20) fail('id read must be 21 rows in one column: '+c); });
// the title is read before the ids of that section, one column left of the id column
if(calls.indexOf('M8!D2')<0||calls.indexOf('M8!K2')<0||calls.indexOf('M8!R2')<0||calls.indexOf('M8!E4:E24')<0||calls.indexOf('M8!S54:S74')<0) fail('title / id ranges '+calls.filter(c=>c.indexOf('M8')===0).join(' '));
// B3 stops at section 2 (no title at K27): section 3's B3 title (K52) and ids (L54:L74) and everything after are never read
if(calls.indexOf('M8!K27')<0) fail('B3 title of section 2 should have been checked');
if(calls.some(c=>/^M8!(K52|L54)/.test(c))) fail('B3 walk must stop at the first missing title');
// B2 stops at section 4 (no title at D77): the title after the gap (D102) is never reached
if(calls.indexOf('M8!D77')<0) fail('B2 title of section 4 should have been checked');
if(calls.some(c=>/^M8!(D102|E104)/.test(c))) fail('B2 walk must stop at the first missing title');
// a tab with no titles: only the three first-section title cells are read, no ids
if(calls.filter(c=>c.indexOf('M3!')===0).join(' ')!=='M3!D2 M3!K2 M3!R2') fail('tab without titles: '+calls.filter(c=>c.indexOf('M3!')===0).join(' '));
// a missing tab costs one read
if(calls.filter(c=>c.indexOf('M99!')===0).length!==1) fail('missing tab must stop at the first read');

// ---- the 60-section cap
calls=[]; r=ctx.collectUsedIds_(['M5'],safeRead);
eq(r.perTab,{M5:60},'cap: 60 sections'); eq(r.ids.length,60,'cap: 60 ids'); eq(r.ids[0],'100001','cap first'); eq(r.ids[59],'100060','cap last');
if(r.ids.indexOf('100061')>=0) fail('section 61 must not be read');
if(calls.indexOf('M5!D1477')<0||calls.indexOf('M5!E1479:E1499')<0) fail('section 60 should be read (D1477, E1479:E1499)');
if(calls.some(c=>/^M5!(D1502|E1504)/.test(c))) fail('section 61 must not even be looked at: '+calls.filter(c=>/^M5!(D15|E15)/.test(c)).join(' '));

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
const mkSheet=(tab)=>guard('sheet',{getName:()=>tab,getRange:(a1)=>mkRange(tab,a1)});
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

// ---- static proof: the read-only helper never even mentions a write method
['collectUsedIds_','usedIdOf_','moduleTabNames_','usedIdsHtml_','escapeHtml_','showUsedIds'].forEach(n=>{
  const src=ctx[n].toString(); const bad=src.match(/\.(set(?!Width|Height)\w*|clear\w*|delete\w*|insert\w*|append\w*|remove\w*|sort\w*Range|copy\w*|merge\w*|activate\w*|protect\w*)\(/g);
  if(bad) fail(n+' calls a write-looking method: '+bad.join(','));
});
console.log('apps script used-ids tests OK');
