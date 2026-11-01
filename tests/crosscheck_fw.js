// Feeds firmware-generated serial captures (from the host simulation of Game_v2.cpp) through the page's real parseLine/handleLine.
const {chromium}=require('playwright');const fs=require('fs'),path=require('path');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage();
  await p.goto('file://'+path.resolve(__dirname,'..','Game_website_v2.html'));
  let bad=0;
  for(const f of process.argv.slice(2)){
    const lines=fs.readFileSync(f,'utf8').split('\n').map(s=>s.trim()).filter(Boolean);
    const r=await p.evaluate(ls=>{resetStream();input.mode='serial';let data=0,info=0,rej=0,flagged=0,maxv=0;
      for(const l of ls){if(l[0]==='#'){info++;continue}const q=parseLine(l);if(!q){rej++;continue}data++;if(q.flags)flagged++;for(const v of q.vals)maxv=Math.max(maxv,v)}
      return {data,info,rej,flagged,maxv,sample:ls.find(l=>l[0]!=='#')}},lines);
    console.log(path.basename(f),JSON.stringify(r));if(r.rej>0)bad++;
  }
  await b.close();process.exit(bad?1:0);
})();
