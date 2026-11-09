const fs=require('fs'); const vm=require('vm');
const ctx={console,String,Number,Math,Object,JSON}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync('integrations/google_sheets_apps_script.gs','utf8')+'\nthis.buildPlan_=buildPlan_;this.describe_=describe_;this.CONFIG=CONFIG;',ctx);
const mk=(mod,label,stage,ids,qty=1)=>({stage,contents:ids.map(i=>({object_id:i,quantity:qty})),builder:{module:mod,label}});
const sheet={M8:{}};
const read=(tab,a1)=>{ if(!sheet[tab])return null; const m=a1.match(/([A-Z])(\d+):([A-Z])(\d+)/); const out=[]; for(let r=+m[2];r<=+m[4];r++) out.push([sheet[tab][m[1]+r]??'']); return out; };
const ids21=Array.from({length:21},(_,i)=>String(4700+i));
let p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21,5),mk('M17','Box A1','A1',ids21),mk('M8','Box B2','B2',ids21)],read);
console.log(ctx.describe_(p)); 
if(p.writes.length!==1||p.writes[0].qtys[0][0]!==3||p.writes[0].ids[0][0]!=='004700') throw 'bad write plan';
if(p.skipped.length!==2) throw 'skips';
// block already filled with the same ids -> same; with something else -> skipped, never overwritten
ids21.forEach((x,i)=>{sheet.M8['S'+(4+i)]="'"+x; });
sheet.M8={}; ids21.forEach((x,i)=>{sheet.M8['S'+(4+i)]=('000000'+x).slice(-6); sheet.M8['V'+(4+i)]=2;});
p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21)],read); if(p.same.length!==1||p.writes.length) throw 'same';
sheet.M8['S10']='123456'; p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21)],read); if(p.skipped.length!==1||p.writes.length) throw 'overwrite guard';
// a single stray cell in the qty column also blocks the write
sheet.M8={V9:7}; p=ctx.buildPlan_([mk('M8','Box A1','A1',ids21)],read); if(p.writes.length) throw 'qty guard';
console.log('apps script plan tests OK');
