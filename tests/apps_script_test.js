const fs=require('fs'); const vm=require('vm');
const ctx={console,String,Number,Math,Object,JSON,parseInt,RegExp}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync('integrations/google_sheets_apps_script.gs','utf8')+'\nthis.buildPlan_=buildPlan_;this.describe_=describe_;this.CONFIG=CONFIG;this.sectionOf_=sectionOf_;this.colShift_=colShift_;',ctx);

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
