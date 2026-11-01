// End-to-end test of Game_website_v2.html in headless Chromium with a *virtual glove*:
// checksummed v2 lines are fed to handleLine() at 100 Hz exactly as the Web Serial reader would.
// Run:  NODE_PATH=/opt/node22/lib/node_modules node tests/e2e_page.js
const {chromium}=require('playwright');
const path=require('path');
const URL='file://'+path.resolve(__dirname,'..','Game_website_v2.html');
let pass=0,fail=0;const fails=[];
const ok=(c,m)=>{if(c){pass++;console.log('  ok   '+m)}else{fail++;fails.push(m);console.log('  FAIL '+m)}};
(async()=>{
  const b=await chromium.launch();
  const ctx=await b.newContext({acceptDownloads:true,viewport:{width:1280,height:800}});
  const p=await ctx.newPage();
  const errs=[];
  p.on('pageerror',e=>errs.push('pageerror: '+e.message));
  p.on('console',m=>{if(m.type()==='error')errs.push('console.error: '+m.text())});
  p.on('dialog',d=>{errs.push('dialog: '+d.message());d.dismiss()});
  await p.goto(URL);await p.waitForTimeout(300);

  console.log('[1] unit checks');
  const U=await p.evaluate(()=>{
    const r={};
    const mk=(body)=>{let x=0;for(const c of body)x^=c.charCodeAt(0);return body+'*'+x.toString(16).toUpperCase().padStart(2,'0')};
    const good=mk('12,1234.567,1,2,3,4,5,6,7,8,9,10,0');
    r.good=parseLine(good);
    r.badcs=parseLine(good.slice(0,-2)+'00');
    r.trunc=parseLine(good.slice(0,20));
    r.legacy=parseLine('1234.5,1,2,3,4,5,6,7,8,9,10');
    r.range=parseLine(mk('1,1.0,1,2,3,4,5,6,7,8,9,99999,0'));
    r.nan=parseLine(mk('1,1.0,1,2,3,4,5,6,7,8,9,abc,0'));
    r.empty=parseLine(mk('1,1.0,1,2,3,4,5,6,7,8,9,,0'));
    r.short=parseLine(mk('1,1.0,1,2,3,0'));
    // filters
    const step=(mode,fc,ds,n=400)=>{const c=new Chain(mode,fc,ds);const y=[];for(let i=0;i<n;i++)y.push(c.step(i<20?0:1000));return y};
    for(const m of ['bw2','bw4','ma']){const y=step(m,5,false);r['dc_'+m]=y[399];r['t90_'+m]=y.findIndex((v,i)=>i>=20&&v>=900)-20;r['pre_'+m]=y[19]}
    // start-up: first sample 2000 -> output stays 2000 (no transient)
    {const c=new Chain('bw2',5,true);let mx=0;for(let i=0;i<50;i++)mx=Math.max(mx,Math.abs(c.step(2000)-2000));r.startup=mx}
    // spike rejection
    {const c=new Chain('bw2',10,true),c2=new Chain('bw2',10,false);let a=0,bb=0;for(let i=0;i<300;i++){const x=1000+(i===150?3000:0);a=Math.max(a,Math.abs(c.step(x)-1000));bb=Math.max(bb,Math.abs(c2.step(x)-1000))}r.spikeWith=a;r.spikeWithout=bb}
    // attenuation of 20 Hz tone, passes 1 Hz
    const tone=(f,mode,fc)=>{const c=new Chain(mode,fc,false);let mx=0;for(let i=0;i<1000;i++){const y=c.step(Math.sin(2*Math.PI*f*i/100));if(i>500)mx=Math.max(mx,Math.abs(y))}return mx};
    r.att20_bw2=tone(20,'bw2',5);r.att20_bw4=tone(20,'bw4',5);r.pass1=tone(1,'bw2',5);
    // measured group delay of a 0.5 Hz sine vs theory
    const delay=(mode,fc)=>{const c=new Chain(mode,fc,false);const f=.5;let best=0,bl=1e9;const out=[];for(let i=0;i<1000;i++)out.push(c.step(Math.sin(2*Math.PI*f*i/100)));
      for(let lag=0;lag<60;lag++){let e=0;for(let i=300;i<900;i++)e+=Math.pow(out[i]-Math.sin(2*Math.PI*f*(i-lag)/100),2);if(e<bl){bl=e;best=lag}}return best*10};
    r.dl_bw2=delay('bw2',5);r.th_bw2=theoDelayMs('bw2',5,false);
    r.dl_bw4=delay('bw4',5);r.th_bw4=theoDelayMs('bw4',5,false);
    // fft
    const N=1024,re=new Float64Array(N),im=new Float64Array(N);
    // spectrum()
    const x=new Float32Array(N);for(let i=0;i<N;i++)x[i]=500+100*Math.sin(2*Math.PI*12.5*i/100);
    const sp=spectrum(x);let pk=0;for(let i=2;i<sp.length;i++)if(sp[i]>sp[pk])pk=i;r.fftPeakHz=pk*100/N;r.fftAmp=sp[pk];
    // FSR round trip
    r.fsrRT=mvToN(nToMv(5,0),0);
    return r;
  });
  ok(U.good&&U.good.seq===12&&U.good.vals.length===10&&U.good.vals[9]===10,'parseLine accepts a valid v2 line');
  ok(U.badcs===null,'parseLine rejects wrong checksum');
  ok(U.trunc===null,'parseLine rejects truncated line');
  ok(U.legacy&&U.legacy.legacy&&U.legacy.vals.length===10,'parseLine accepts legacy line');
  ok(U.range===null,'parseLine rejects out-of-range value');
  ok(U.nan===null&&U.empty===null&&U.short===null,'parseLine rejects NaN / empty field / wrong field count');
  for(const m of ['bw2','bw4','ma'])ok(Math.abs(U['dc_'+m]-1000)<1&&U['pre_'+m]===0,`${m}: unity DC gain, no pre-step output`);
  ok(U.startup<1e-6,'filter starts at first sample without transient ('+U.startup+')');
  ok(U.spikeWith<400&&U.spikeWithout>400,`median despike removes a 1-sample spike (with ${U.spikeWith.toFixed(0)} mV, without ${U.spikeWithout.toFixed(0)} mV)`);
  ok(U.att20_bw2<.1&&U.att20_bw4<U.att20_bw2&&U.pass1>.9,`20 Hz attenuated (bw2 ${U.att20_bw2.toFixed(3)}, bw4 ${U.att20_bw4.toFixed(3)}), 1 Hz passed ${U.pass1.toFixed(2)}`);
  ok(Math.abs(U.dl_bw2-U.th_bw2)<=15&&Math.abs(U.dl_bw4-U.th_bw4)<=15,`measured delay matches theory (bw2 ${U.dl_bw2} vs ${U.th_bw2.toFixed(0)} ms; bw4 ${U.dl_bw4} vs ${U.th_bw4.toFixed(0)} ms)`);
  ok(Math.abs(U.fftPeakHz-12.5)<.2&&Math.abs(U.fftAmp-100)<8,`FFT peak ${U.fftPeakHz.toFixed(2)} Hz, amplitude ${U.fftAmp.toFixed(1)} mV (expected 12.5 / 100)`);
  ok(Math.abs(U.fsrRT-5)<.05,'FSR mV<->N round trip');

  console.log('[2] virtual glove');
  await p.evaluate(()=>{
    window.G={flex:[0,0,0,0,0],grip:0,pinch:0,tip:[0,0,0,0],seq:0,on:true,mute:false,noise:2,corrupt:0,iv:null,sent:0,t0:performance.now()};
    window.mkLine=body=>{let x=0;for(let i=0;i<body.length;i++)x^=body.charCodeAt(i);return body+'*'+x.toString(16).toUpperCase().padStart(2,'0')};
    G.iv=setInterval(()=>{
      if(!G.on||G.mute)return;
      // catch-up so that 100 Hz holds despite timer jitter
      const due=Math.floor((performance.now()-G.t0)/10);
      while(G.sent<due){
        G.sent++;
        const v=[];
        for(let i=0;i<5;i++)v.push(1200+G.flex[i]*1300+(Math.random()-.5)*2*G.noise*3);
        for(let j=0;j<4;j++)v.push(G.tip[j]>0?nToMv(G.tip[j],j)+(Math.random()-.5)*G.noise*3:0);
        v.push(G.grip>0?nToMv(G.grip,4)+(Math.random()-.5)*G.noise*3:0);
        // pinch on thumb tip (index 5) is the thumb FSR
        if(G.pinch>0)v[5]=nToMv(G.pinch,0);
        const body=`${G.seq++},${((performance.now()-G.t0)).toFixed(3)},${v.map(x=>x.toFixed(1)).join(',')},0`;
        let line=mkLine(body);
        if(G.corrupt&&Math.random()<G.corrupt){line=line.slice(0,8)+'9'+line.slice(9)}
        handleLine(line);
      }
    },4);
    resetStream();buildFilters();input.mode='serial';input.wantGlove=true;
  });
  await p.waitForTimeout(2200);
  let S=await p.evaluate(()=>({ok:sigOK(),rate:input.rate,lines:dev.lines,drops:dev.drops,bad:dev.bad,problem:sigProblem()}));
  ok(S.ok&&S.rate>85&&S.rate<115,`signal OK at ${S.rate.toFixed(0)} Hz`);

  // 2b. Lab screen first (short recording) while idle
  console.log('[3] signal lab');
  await p.click('#btnLab');await p.waitForTimeout(300);
  await p.evaluate(()=>{G.flex=[0,0,0,0,0]});
  await p.click('#btnLabRec');await p.waitForTimeout(3000);
  await p.evaluate(()=>{G.corrupt=.05});await p.waitForTimeout(1500);await p.evaluate(()=>{G.corrupt=0});
  await p.click('#btnLabRec');await p.waitForTimeout(200);
  const lab1=await p.evaluate(()=>({n:lab.rows.length,on:lab.on,csvDis:$('#btnLabCsv').disabled,meta:lab.meta&&{bad:lab.meta.bad,lost:lab.meta.lost}}));
  ok(!lab1.on&&lab1.n>380&&lab1.n<520,`lab recorded ${lab1.n} rows in ~4.5 s`);
  ok(!lab1.csvDis,'lab CSV button enabled after recording');
  ok(lab1.meta.bad>0,`lab counted corrupt lines (${lab1.meta.bad})`);
  const [dl]=await Promise.all([p.waitForEvent('download'),p.click('#btnLabCsv')]);
  const labCsv=require('fs').readFileSync(await dl.path(),'utf8');
  const L=labCsv.split('\n');const hdrI=L.findIndex(l=>l.startsWith('t_s,'));
  const cols=L[hdrI].split(',');
  ok(hdrI>3&&cols.length===23,`lab CSV header has ${cols.length} columns, ${hdrI} comment lines`);
  ok(L.slice(hdrI+1).filter(Boolean).every(r=>r.split(',').length===cols.length),'lab CSV rows all have the same column count');
  require('fs').writeFileSync('/tmp/lab_test.csv',labCsv);
  console.log('     ',dl.suggestedFilename());
  await p.click('#lab [data-go=home]');

  console.log('[4] session flow');
  await p.fill('#pid','T01');await p.dispatchEvent('#pid','change');
  await p.click('#btnStart');await p.waitForTimeout(200);
  await p.click('.yn[data-q=flare] button[data-v=no]');
  if(await p.isVisible('#lingerCard'))await p.click('.yn[data-q=linger] button[data-v=no]');
  await p.click('#btnSafetyNext');
  await p.click('#painBtns button:nth-child(2)');await p.click('#btnPainNext');
  await p.waitForTimeout(200);
  // calibration with the glove driven by the test
  const calPlan=[
    ()=>{G.flex=[0,0,0,0,0]},
    ()=>{G.flex=[1,1,1,1,1]},
    ()=>{G.flex=[0,0,0,0,0];G.grip=18},
    ()=>{G.grip=0;G.pinch=5},
  ];
  for(let s=0;s<4;s++){
    await p.evaluate(`(${calPlan[s].toString()})()`);
    await p.waitForTimeout(400);
    await p.click('#btnCalRec');
    await p.waitForTimeout((s===0?2:3)*1000+900);
  }
  await p.evaluate(()=>{G.flex=[0,0,0,0,0];G.grip=0;G.pinch=0});
  await p.waitForTimeout(700);
  const C=await p.evaluate(()=>({menu:$('#menu').classList.contains('on'),flat:cal.flat.map(Math.round),fist:cal.fist.map(Math.round),grip:cal.gripMax,pinch:cal.pinchMax,msg:$('#calMsg').textContent}));
  ok(C.menu,'calibration finished and menu shown ('+C.msg+')');
  ok(C.flat.every(x=>Math.abs(x-1200)<20)&&C.fist.every(x=>Math.abs(x-2500)<60),`flat ${C.flat} fist ${C.fist}`);
  ok(Math.abs(C.grip-18)<3&&Math.abs(C.pinch-5)<1.5,`grip max ${C.grip.toFixed(1)} N (18), pinch max ${C.pinch.toFixed(1)} N (5)`);

  console.log('[5] games, signal loss, adaptation');
  const keys=['jelly','piano','firefly','balloon','egg'];
  for(let gi=0;gi<5;gi++){
    const cards=await p.$$('.game-card');
    await cards[gi].click();await p.waitForTimeout(300);
    // wave the hand a bit so something happens
    await p.evaluate(()=>{window.wave=setInterval(()=>{const t=performance.now()/1000;const f=.5+.5*Math.sin(t*1.5);G.flex=[f,f,f,f,f];G.grip=f*15;G.pinch=f*4;G.tip=[f*2,f*2,f*2,f*2]},30)});
    await p.waitForTimeout(2500);
    if(gi===0||gi===3){
      // drop the glove for 1.2 s
      const before=await p.evaluate(()=>({pa:pauseAcc}));
      await p.evaluate(()=>{G.mute=true});await p.waitForTimeout(1200);
      const during=await p.evaluate(()=>({ps:pausedSince,prob:sigProblem(),t:cur.hud()}));
      await p.evaluate(()=>{G.mute=false});await p.waitForTimeout(600);
      const after=await p.evaluate(()=>({pa:pauseAcc,ps:pausedSince,evs:session.events.filter(e=>e[2]==='paused'||e[2]==='resumed').map(e=>e.join('|'))}));
      ok(during.ps!=null,`${keys[gi]}: game paused on signal loss (${during.prob})`);
      const lost=after.pa-before.pa;   // 1.2 s outage minus the 0.35 s detection delay
      ok(after.ps==null&&lost>.6&&lost<1.1,`${keys[gi]}: resumed, game clock lost ${lost.toFixed(2)} s`);
      ok(after.evs.length>=2,`${keys[gi]}: paused/resumed events logged`);
    }
    await p.evaluate(()=>{clearInterval(window.wave)});
    if(gi%2===0){await p.evaluate(()=>{cur.done=true});}   // exercise the normal-completion path
    else await p.click('#btnQuit');
    await p.waitForTimeout(500);
    const R=await p.evaluate(()=>({rest:$('#rest').classList.contains('on'),title:$('#restTitle').textContent,rows:$('#restTable').innerText}));
    ok(R.rest,`${keys[gi]}: result screen "${R.title}"`);
    await p.click('#btnRestSkip');await p.waitForTimeout(200);
  }
  const P=await p.evaluate(()=>({prog:JSON.parse(JSON.stringify(prog)),att:session.attempts.map(a=>[a.key,a.quit,a.P==null?null:+a.P.toFixed(2),a.level,a.adapt&&a.adapt.action])}));
  console.log('     attempts:',JSON.stringify(P.att));console.log('     prog:',JSON.stringify(P.prog));
  ok(P.att.length===5,'5 attempts recorded');

  console.log('[6] finish, summary, exports');
  await p.click('#btnFinish');await p.waitForTimeout(200);
  await p.click('#painBtns button:nth-child(4)');await p.click('#btnPainNext');await p.waitForTimeout(300);
  const SUM=await p.evaluate(()=>({on:$('#summary').classList.contains('on'),notes:$('#sumNotes').innerText}));
  ok(SUM.on,'summary shown');console.log('     '+SUM.notes.replace(/\n+/g,'\n      '));
  const out={};
  for(const [id,nm] of [['#btnCsvSum','sum'],['#btnCsvEv','ev'],['#btnCsvRaw','raw']]){
    const [d]=await Promise.all([p.waitForEvent('download'),p.click(id)]);
    out[nm]={name:d.suggestedFilename(),txt:require('fs').readFileSync(await d.path(),'utf8')};
  }
  const rawL=out.raw.txt.split('\n');const rh=rawL.findIndex(l=>l.startsWith('t_s,'));const rc=rawL[rh].split(',');
  const rrows=rawL.slice(rh+1).filter(Boolean);
  ok(rc.length===rrows[0].split(',').length&&rrows.every(r=>r.split(',').length===rc.length),`raw CSV consistent: ${rc.length} columns × ${rrows.length} rows`);
  ok(rrows.length>1000,'raw CSV has samples');
  const ts=rrows.map(r=>+r.split(',')[0]);ok(ts.every((t,i)=>i===0||t>=ts[i-1]),'raw CSV timestamps monotonic');
  ok(/pause/.test(out.ev.txt)&&/adapt/.test(out.ev.txt)&&/calibration/.test(out.ev.txt),'events CSV has calibration, pause and adapt events');
  ok(/normalised_score/.test(out.sum.txt),'summary CSV written');
  console.log('     files:',Object.values(out).map(o=>o.name).join(', '));
  require('fs').writeFileSync('/tmp/raw_test.csv',out.raw.txt);require('fs').writeFileSync('/tmp/ev_test.csv',out.ev.txt);require('fs').writeFileSync('/tmp/sum_test.csv',out.sum.txt);

  console.log('[7] page errors');
  const real=errs.filter(e=>!/ERR_TUNNEL|ERR_NAME|ERR_INTERNET|Failed to load resource/.test(e));ok(real.length===0,real.length?real.join(' || '):'no page errors / console errors / dialogs');
  console.log(`\n${pass} passed, ${fail} failed`);if(fail)console.log(fails.join('\n'));
  await b.close();process.exit(fail?1:0);
})().catch(e=>{console.error('TEST CRASH',e);process.exit(2)});
