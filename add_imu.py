#!/usr/bin/env python3
"""
add_imu.py - adds an MPU-6050 "IMU Lab" page to FlexPlay and MPU-6050 streaming to the glove firmware.

  python3 add_imu.py Game_website_v2.html Game_v2.cpp
  -> writes Game_website_v3.html and Game_v3.cpp next to the inputs (originals untouched).

Every edit is an exact-text anchor; the script stops with a message if an anchor is missing.

New serial line (extra line after each normal sample, same seq):
  M,seq,t_ms,ax,ay,az,gx,gy,gz,temp,flags*HH     raw int16 counts, XOR checksum like the main line
  +-4 g = 8192 LSB/g, +-500 deg/s = 65.5 LSB/(deg/s), flags bit0 = MPU read failed
Wiring: MPU-6050 on the same I2C bus (SDA 21, SCL 22), AD0 -> GND (address 0x68). No conflict with 0x48/0x49/0x4A.
"""
import sys, os

# ====================================================================== HTML
CSS_OLD = "#labcv{height:230px}#speccv{height:200px}"
CSS_NEW = CSS_OLD + "#imucv{height:230px}#imuspec{height:200px}table.an td,table.an th{font-size:13px}"

HOME_OLD = '<button class="ghost" id="btnLab">Signal Lab</button>'
HOME_NEW = HOME_OLD + '\n    <button class="ghost" id="btnImu">IMU Lab</button>'

SECTION = r"""
<!-- IMU LAB -->
<section class="screen" id="imu"><div class="wrap">
  <div class="row"><h2 style="margin:0">IMU Lab</h2><span class="chip" id="imuStatus">no data</span></div>
  <p>MPU-6050 accelerometer (±4 g) and gyroscope (±500 °/s) at 100 Hz: live scope, spectrum, tremor-band metrics, orientation and condition-labelled recording. With no glove connected it runs on a simulated hand.</p>
  <div class="card">
    <div class="row"><label style="margin:0">Channel</label><select id="imuCh"></select>
      <label style="margin:0">Filter</label><select id="imuFilt"><option value="none">None (raw)</option><option value="ma">Moving average</option><option value="bw2">Butterworth 2nd order</option><option value="bw4">Butterworth 4th order</option></select>
      <label style="margin:0">cut-off Hz</label><input type="number" id="imuFc" min=".5" max="40" step=".5" style="width:80px"></div>
    <canvas class="lab" id="imucv"></canvas>
    <p class="small" id="imuNums"></p>
    <canvas class="lab" id="imuspec"></canvas>
    <p class="small" id="imuTrem"></p>
    <p class="small" style="margin-bottom:0">Spectrum of the last 5 s of the raw signal (dB re 1 unit). Gravity (0 Hz) is removed. Parkinsonian rest tremor is typically 4–6 Hz and essential or physiological tremor 6–12 Hz; the 4–12 Hz band is reported. The MPU-6050 low-pass is set to 44 Hz in the firmware, so mains and vibration above that is attenuated before sampling.</p>
  </div>
  <div class="card">
    <h3 style="margin-top:0">Orientation and gyro bias</h3>
    <p class="small" id="imuOri"></p>
    <div class="row"><button id="btnImuBias">Zero gyro bias (hold still 3 s)</button><span class="small" id="imuBiasMsg"></span></div>
  </div>
  <div class="card" id="imuSimCard">
    <h3 style="margin-top:0">Simulated hand</h3>
    <div class="row"><label style="margin:0"><input type="checkbox" id="isTrem" checked>tremor</label>
      <label style="margin:0">Hz</label><input type="number" id="isF" value="6" min="1" max="20" step=".5" style="width:80px">
      <label style="margin:0">amplitude (mg)</label><input type="number" id="isA" value="50" min="0" max="500" step="5" style="width:80px"></div>
  </div>
  <div class="card">
    <h3 style="margin-top:0">Record</h3>
    <div class="row"><label style="margin:0">Condition</label>
      <select id="imuCond"><option>Still (noise floor)</option><option>Hand held still (posture)</option><option>Rest tremor</option><option>Postural tremor (arm outstretched)</option><option>Wrist flexion-extension slow</option><option>Wrist flexion-extension fast</option><option>Walking</option><option>Custom</option></select>
      <button id="btnImuRec" class="primary">● Start recording</button><span class="chip" id="imuElapsed">not recording</span></div>
    <p class="small" id="imuMsg"></p>
    <div style="overflow-x:auto"><table class="an" id="imuTable"></table></div>
    <p class="small">The table analyses the raw (unfiltered) recording: mean, RMS and MAV of the AC part, peak-to-peak, the strongest frequency between 3 and 12 Hz and the RMS inside 4–12 Hz (Welch, 2.56 s Hann segments). Gyro values are bias-corrected. Units: g and °/s.</p>
    <div class="row"><button id="btnImuCsv" disabled>Download CSV</button><button class="ghost" id="btnImuClear" disabled>Discard</button><button class="ghost" data-go="home">Back</button></div>
  </div>
</div></section>
"""

# ====================================================================== JS
JS = r"""
// ---------------------------------------------------------------- IMU Lab (MPU-6050)
const ACC_LSB=8192,GYR_LSB=65.5;   // +-4 g and +-500 deg/s, must match the firmware
const IMU_CH=['ax (g)','ay (g)','az (g)','gx (°/s)','gy (°/s)','gz (°/s)','|a| (g)','|ω| (°/s)'];
const IMU_U=['g','g','g','°/s','°/s','°/s','g','°/s'];
const imu={c:new Array(7).fill(0),v:new Array(8).fill(0),f:new Array(8).fill(0),temp:0,seq:-1,drops:0,bad:0,lines:0,flags:0,fault:false,lastRx:0,lastT:null,lastTd:null,
  init:false,roll:0,pitch:0,rollA:0,pitchA:0,rate:0,rc:0,rt0:now(),pos:0,n:0,biasRec:null,
  raw:Array.from({length:8},()=>new Float32Array(RING)),filt:Array.from({length:8},()=>new Float32Array(RING))};
const imuRec={on:false,rows:[],t0:0,meta:null,notes:[]};
let imuF=[];
function buildImuF(){imuF=Array.from({length:6},()=>new Chain(settings.imuFilt,settings.imuFc,false))}
buildImuF();
function imuReset(){Object.assign(imu,{seq:-1,drops:0,bad:0,lines:0,flags:0,fault:false,lastRx:0,lastT:null,lastTd:null})}
function imuLine(line){   // M,seq,t_ms,ax,ay,az,gx,gy,gz,temp,flags*HH
  const star=line.lastIndexOf('*');if(star<0){imu.bad++;return}
  const cs=line.slice(star+1).trim();if(!/^[0-9A-Fa-f]{2}$/.test(cs)){imu.bad++;return}
  const body=line.slice(0,star);let x=0;for(let i=0;i<body.length;i++)x^=body.charCodeAt(i);
  if(x!==parseInt(cs,16)){imu.bad++;return}
  const f=body.split(',');if(f.length!==11||f.slice(1).some(s=>s.trim()==='')){imu.bad++;return}
  const n=f.slice(1).map(Number);if(n.some(v=>!isFinite(v))||n.slice(2,9).some(v=>v<-32768||v>32767)){imu.bad++;return}
  if(imu.seq>=0){const d=(n[0]-imu.seq)>>>0;if(d===0)return;if(d>1&&d<1000)imu.drops+=d-1}
  imu.seq=n[0];imu.lines++;imu.lastRx=now();imu.flags=n[9];imu.fault=!!(n[9]&1);
  if(imu.fault)return;
  imuSample(imu.lastRx,n.slice(2,9),{seq:n[0],tdev:n[1]});
}
function imuSample(t,c,meta){
  const v=new Array(8),f=new Array(8),b=settings.imuBias;
  for(let i=0;i<3;i++){v[i]=c[i]/ACC_LSB;v[3+i]=c[3+i]/GYR_LSB-b[i]}
  v[6]=Math.hypot(v[0],v[1],v[2]);v[7]=Math.hypot(v[3],v[4],v[5]);
  if(imu.biasRec){imu.biasRec.n++;for(let i=0;i<3;i++)imu.biasRec.s[i]+=c[3+i]/GYR_LSB}
  for(let i=0;i<6;i++)f[i]=imuF[i].step(v[i]);
  f[6]=Math.hypot(f[0],f[1],f[2]);f[7]=Math.hypot(f[3],f[4],f[5]);
  imu.c=c;imu.v=v;imu.f=f;imu.temp=c[6]/340+36.53;
  // complementary filter: gyro for fast changes, accelerometer for the slow gravity reference (axis signs depend on how the board is mounted)
  let dt=meta.tdev!=null&&imu.lastTd!=null?(meta.tdev-imu.lastTd)/1000:(imu.lastT!=null?t-imu.lastT:.01);
  dt=clamp(dt,.002,.05);imu.lastT=t;imu.lastTd=meta.tdev!=null?meta.tdev:null;
  const D=180/Math.PI,rA=Math.atan2(v[1],v[2])*D,pA=Math.atan2(-v[0],Math.hypot(v[1],v[2]))*D;
  if(!imu.init){imu.roll=rA;imu.pitch=pA;imu.init=true}
  else{imu.roll=.98*(imu.roll+v[3]*dt)+.02*rA;imu.pitch=.98*(imu.pitch+v[4]*dt)+.02*pA}
  imu.rollA=rA;imu.pitchA=pA;
  for(let i=0;i<8;i++){imu.raw[i][imu.pos]=v[i];imu.filt[i][imu.pos]=f[i]}
  imu.pos=(imu.pos+1)%RING;imu.n=Math.min(RING,imu.n+1);
  if(imuRec.on){if(imuRec.rows.length<180000)imuRec.rows.push([t-imuRec.t0,meta.seq!=null?meta.seq:-1,meta.tdev!=null?meta.tdev:-1,...c,...f.slice(0,6)]);else stopImu('Stopped automatically at 30 minutes.')}
  imu.rc++;if(t-imu.rt0>=1){imu.rate=imu.rc/(t-imu.rt0);imu.rc=0;imu.rt0=t}
}
// simulated hand: slow tilt + optional tremor + gyro bias, so the page can be rehearsed with no hardware
let imuSimN=0;
setInterval(()=>{
  if(input.mode!=='sim'||input.wantGlove||!$('#imu').classList.contains('on'))return;
  const t=now(),nz=s=>(Math.random()-.5)*s,tilt=.35*Math.sin(t*.5);
  const tr=$('#isTrem').checked?(+$('#isA').value||0)/1000*Math.sin(2*Math.PI*(+$('#isF').value||6)*t):0;
  const a=[-Math.sin(tilt)+tr+nz(.01),Math.sin(tilt*.7)*.2+tr*.6+nz(.01),Math.cos(tilt)+nz(.01)];
  const g=[1.5+nz(.4),10*Math.cos(t*.5)+tr*80+nz(.4),-.8+nz(.4)];
  imu.lastRx=t;imu.lines++;imu.fault=false;
  imuSample(t,[...a.map(x=>Math.round(x*ACC_LSB)),...g.map(x=>Math.round(x*GYR_LSB)),Math.round((30-36.53)*340)],{seq:imuSimN++});
},10);

// spectra
function welchP(x,N=256){const hop=N/2,acc=new Float64Array(N/2+1);let m=0;
  for(let s=0;s+N<=x.length;s+=hop){const A=spectrum(x.slice(s,s+N));for(let k=0;k<A.length;k++)acc[k]+=A[k]*A[k];m++}
  return m?acc.map(v=>v/m):null}
function bandOf(P,df){let pk=0,pv=-1,s=0;for(let k=1;k<P.length;k++){const f=k*df;if(f>=3&&f<=12&&P[k]>pv){pv=P[k];pk=f}if(f>=4&&f<=12)s+=.5*P[k]}return {pk,rms:Math.sqrt(s)}}
const imuLast=(a,n)=>{const o=new Float32Array(n);for(let i=0;i<n;i++)o[i]=a[(imu.pos-n+i+RING*2)%RING];return o};
const dg=u=>u==='g'?4:2;

// UI
(function(){const s=$('#imuCh');IMU_CH.forEach((l,i)=>{const o=document.createElement('option');o.value=i;o.textContent=l;s.appendChild(o)});s.value=6})();
function imuEnter(){$('#imuFilt').value=settings.imuFilt;$('#imuFc').value=settings.imuFc;imuButtons()}
$('#btnImu').onclick=()=>show('imu');
['#imuFilt','#imuFc'].forEach(s=>$(s).onchange=()=>{
  settings.imuFilt=$('#imuFilt').value;settings.imuFc=clamp(+$('#imuFc').value||15,.5,40);save('flexplay.settings',settings);buildImuF();
  if(imuRec.on)imuRec.notes.push(`${fmtT(imuRec.rows.length?imuRec.rows[imuRec.rows.length-1][0]:0)} filter changed to ${settings.imuFilt} ${settings.imuFc} Hz`);
});
$('#btnImuBias').onclick=()=>{imu.biasRec={n:0,s:[0,0,0],t0:now()};$('#imuBiasMsg').textContent='Keep the sensor still…'};
function imuButtons(){
  const R=imuRec,el=R.rows.length?R.rows[R.rows.length-1][0]:0;
  $('#btnImuRec').textContent=R.on?'■ Stop recording':'● Start recording';$('#btnImuRec').className=R.on?'':'primary';
  $('#imuElapsed').textContent=(R.on||R.rows.length)?`${fmtT(el)} · ${R.rows.length} samples${el>=300?' · ✓ 5 min reached':''}`:'not recording';
  $('#btnImuCsv').disabled=!R.rows.length||R.on;$('#btnImuClear').disabled=R.on||!R.rows.length;
}
function startImu(){
  const cond=$('#imuCond').value;
  Object.assign(imuRec,{rows:[],notes:[],t0:now(),on:true});
  imuRec.meta={pid,cond,slug:cond.replace(/[^A-Za-z0-9]+/g,'-').replace(/^-|-$/g,'').toLowerCase(),start:new Date().toISOString(),source:input.mode,filt:settings.imuFilt,fc:settings.imuFc,bias:settings.imuBias.slice(),lost0:imu.drops,bad0:imu.bad};
  $('#imuTable').innerHTML='';imuButtons();
}
function stopImu(msg){
  imuRec.on=false;const m=imuRec.meta;if(m){m.lost=imu.drops-m.lost0;m.bad=imu.bad-m.bad0}
  $('#imuTable').innerHTML=imuAnalyse();imuButtons();if(msg)$('#imuMsg').textContent=msg;
}
$('#btnImuRec').onclick=()=>{$('#imuMsg').textContent='';
  if(imuRec.on)stopImu();
  else{if(input.mode==='serial'&&!(imu.lines&&now()-imu.lastRx<.5&&!imu.fault)){$('#imuMsg').textContent='No IMU data from the glove.';return}startImu()}};
$('#btnImuClear').onclick=()=>{imuRec.rows=[];imuRec.meta=null;$('#imuTable').innerHTML='';imuButtons()};
$('#btnImuCsv').onclick=()=>{
  const m=imuRec.meta;if(!m||!imuRec.rows.length)return;
  const hdr=['# flexplay imu lab','# participant='+m.pid,'# condition='+m.cond,'# start='+m.start,'# source='+m.source,'# fs_nominal_hz='+FS,
    `# accel_lsb_per_g=${ACC_LSB} gyro_lsb_per_dps=${GYR_LSB} temp_c=count/340+36.53`,`# gyro_bias_dps=${m.bias.map(v=>v.toFixed(4)).join('|')} (subtracted in the filt_ g* columns, not in the raw counts)`,
    `# filter=${m.filt} fc=${m.fc}`,'# device='+(dev.info||'none'),`# lost_lines=${m.lost??imu.drops-m.lost0} corrupt_lines=${m.bad??imu.bad-m.bad0}`,...imuRec.notes.map(n=>'# note '+n)];
  const cols=['t_s','seq','t_dev_ms','raw_ax','raw_ay','raw_az','raw_gx','raw_gy','raw_gz','raw_temp','filt_ax_g','filt_ay_g','filt_az_g','filt_gx_dps','filt_gy_dps','filt_gz_dps'];
  const body=imuRec.rows.map(r=>[r[0].toFixed(4),r[1],r[2]<0?'':r[2],...r.slice(3,10),...r.slice(10).map(v=>v.toFixed(5))].join(',')).join('\n');
  download(`flexplay_imu_${m.pid}_${m.slug}_${m.start.slice(0,16).replace(/[:T]/g,'-')}.csv`,hdr.join('\n')+'\n'+cols.join(',')+'\n'+body);
};
function imuAnalyse(){
  const R=imuRec.rows,m=imuRec.meta;if(R.length<256)return '<tr><td>Record at least 3 s to analyse.</td></tr>';
  const x=[];for(let i=0;i<6;i++)x.push(R.map(r=>i<3?r[3+i]/ACC_LSB:r[3+i]/GYR_LSB-m.bias[i-3]));
  x.push(R.map((_,k)=>Math.hypot(x[0][k],x[1][k],x[2][k])));x.push(R.map((_,k)=>Math.hypot(x[3][k],x[4][k],x[5][k])));
  let h='<tr><th>Channel</th><th>mean</th><th>RMS (ac)</th><th>MAV (ac)</th><th>pk-pk</th><th>peak 3–12 Hz</th><th>4–12 Hz RMS</th></tr>';
  x.forEach((a,i)=>{
    const mu=mean(a),ac=a.map(v=>v-mu),rms=Math.sqrt(mean(ac.map(v=>v*v))),mav=mean(ac.map(v=>Math.abs(v)));
    let mx=-Infinity,mn=Infinity;for(const v of a){if(v>mx)mx=v;if(v<mn)mn=v}
    const d=dg(IMU_U[i]),P=welchP(a),b=P?bandOf(P,FS/256):null;
    h+=`<tr><td>${IMU_CH[i]}</td><td class="v">${mu.toFixed(d)}</td><td class="v">${rms.toFixed(d)}</td><td class="v">${mav.toFixed(d)}</td><td class="v">${(mx-mn).toFixed(d)}</td><td class="v">${b?b.pk.toFixed(1)+' Hz':'–'}</td><td class="v">${b?b.rms.toFixed(d+1):'–'}</td></tr>`;
  });
  const lost=m.lost??0,bad=m.bad??0;
  return h+`<tr><td colspan="7" class="small">${R.length} samples · filter shown on screen: ${m.filt} ${m.fc} Hz · lost lines ${lost}, corrupt ${bad} · mean temperature ${fmt(mean(R.map(r=>r[9]/340+36.53)),1)} °C</td></tr>`;
}
function drawImu(){
  const ch=+$('#imuCh').value,u=IMU_U[ch],d=dg(u),n=Math.min(imu.n,RING);
  // status
  let st,cls='ok';
  if(input.mode==='serial'){
    if(imu.fault){st='MPU-6050 not responding';cls='bad'}
    else if(!imu.lines){st='Waiting for IMU lines (MPU-6050 wired? firmware updated?)';cls='bad'}
    else if(now()-imu.lastRx>.5){st='No IMU data from the glove';cls='bad'}
    else st=`IMU · ${Math.round(imu.rate)} Hz · lost ${imu.drops} · corrupt ${imu.bad}`;
  }else st=`Simulated IMU · ${Math.round(imu.rate)} Hz`;
  const sc=$('#imuStatus');sc.textContent=st;sc.className='chip '+cls;
  $('#imuSimCard').style.display=input.mode==='sim'?'':'none';
  if(imu.biasRec&&now()-imu.biasRec.t0>=3){const b=imu.biasRec;imu.biasRec=null;
    if(b.n>=100){settings.imuBias=b.s.map(v=>v/b.n);save('flexplay.settings',settings);$('#imuBiasMsg').textContent='Bias (°/s): '+settings.imuBias.map(v=>v.toFixed(2)).join(', ')}
    else $('#imuBiasMsg').textContent='Too few samples; check the connection and try again.'}
  const v=imu.v,bi=settings.imuBias;
  $('#imuOri').innerHTML=`Fused roll <b>${imu.roll.toFixed(1)}°</b> · pitch <b>${imu.pitch.toFixed(1)}°</b> (accelerometer only: ${imu.rollA.toFixed(1)}° / ${imu.pitchA.toFixed(1)}°) · temperature ${imu.temp.toFixed(1)} °C · gyro bias ${bi.map(q=>q.toFixed(2)).join(', ')} °/s. Axis signs depend on how the chip is mounted on the glove.`;
  { // scope
    const c=$('#imucv'),g=c.getContext('2d'),W=c.clientWidth|0,H=c.clientHeight|0;if(c.width!==W)c.width=W;if(c.height!==H)c.height=H;g.clearRect(0,0,W,H);
    g.font='12px Nunito,system-ui';g.fillStyle='#9fb3cf';g.textAlign='left';
    if(n<20){g.fillText('Waiting for data…',10,20);return}
    const raw=imuLast(imu.raw[ch],n),fl=imuLast(imu.filt[ch],n);
    let lo=Infinity,hi=-Infinity;for(let i=0;i<n;i++){lo=Math.min(lo,raw[i],fl[i]);hi=Math.max(hi,raw[i],fl[i])}
    const pad=Math.max(1e-3,(hi-lo)*.1);lo-=pad;hi+=pad;
    const X=i=>10+(W-20)*i/(RING-1),Y=q=>H-14-(H-28)*(q-lo)/(hi-lo);
    g.strokeStyle='#1f3454';g.lineWidth=1;for(let k=0;k<=4;k++){const y=14+(H-28)*k/4;g.beginPath();g.moveTo(10,y);g.lineTo(W-10,y);g.stroke();g.fillText((hi-(hi-lo)*k/4).toFixed(d-1)+' '+u,12,y-2)}
    const dr=(a,col,w)=>{g.strokeStyle=col;g.lineWidth=w;g.beginPath();for(let i=0;i<n;i++){const x=X(RING-n+i),y=Y(a[i]);i?g.lineTo(x,y):g.moveTo(x,y)}g.stroke()};
    dr(raw,'#7c8aa3',1);dr(fl,'#ffb84d',2);
    g.fillStyle='#7c8aa3';g.fillText('raw',W-90,H-3);g.fillStyle='#ffb84d';g.fillText('filtered',W-50,H-3);
    const m=Math.min(n,200),r2=raw.slice(n-m),f2=fl.slice(n-m);
    $('#imuNums').innerHTML=`Now: ax <b>${v[0].toFixed(3)}</b> ay <b>${v[1].toFixed(3)}</b> az <b>${v[2].toFixed(3)}</b> g · gx <b>${v[3].toFixed(1)}</b> gy <b>${v[4].toFixed(1)}</b> gz <b>${v[5].toFixed(1)}</b> °/s · |a| <b>${v[6].toFixed(3)}</b> g.`+
      ` Selected channel, last 2 s: mean <b>${mean(r2).toFixed(d)}</b> · raw std <b>${std(r2).toFixed(d+1)}</b> · filtered std <b>${std(f2).toFixed(d+1)}</b> ${u}.`;
  }
  { // spectrum of the last 5.12 s
    const c=$('#imuspec'),g=c.getContext('2d'),W=c.clientWidth|0,H=c.clientHeight|0;if(c.width!==W)c.width=W;if(c.height!==H)c.height=H;g.clearRect(0,0,W,H);
    g.font='12px Nunito,system-ui';g.textAlign='left';g.fillStyle='#9fb3cf';
    if(imu.n<512){g.fillText('Spectrum needs 5 s of data…',10,20);$('#imuTrem').textContent='';return}
    const sr=spectrum(imuLast(imu.raw[ch],512)),sf=spectrum(imuLast(imu.filt[ch],512));
    const dB=a=>20*Math.log10(a+1e-9);let LO=Infinity,HI=-Infinity;for(const a of sr){const q=dB(a);if(q<LO)LO=q;if(q>HI)HI=q}LO=Math.floor(LO/10)*10;HI=Math.ceil(HI/10)*10+10;if(HI-LO<30)LO=HI-30;
    const X=k=>30+(W-40)*k/(sr.length-1),Y=q=>14+(H-30)*(1-(clamp(q,LO,HI)-LO)/(HI-LO));
    g.strokeStyle='#1f3454';g.lineWidth=1;
    for(let f=0;f<=50;f+=10){const x=X(f/50*(sr.length-1));g.beginPath();g.moveTo(x,14);g.lineTo(x,H-16);g.stroke();g.fillText(f+' Hz',x-10,H-3)}
    for(let q=LO;q<=HI;q+=10){const y=Y(q);g.beginPath();g.moveTo(30,y);g.lineTo(W-10,y);g.stroke();g.fillText(q+' dB',0,y-2)}
    const x0=X(4/50*(sr.length-1)),x1=X(12/50*(sr.length-1));g.fillStyle='rgba(95,211,155,.12)';g.fillRect(x0,14,x1-x0,H-30);
    const dr=(a,col,w)=>{g.strokeStyle=col;g.lineWidth=w;g.beginPath();a.forEach((q,k)=>{const x=X(k),y=Y(dB(q));k?g.lineTo(x,y):g.moveTo(x,y)});g.stroke()};
    dr(sr,'#7c8aa3',1);dr(sf,'#ffb84d',2);
    const b=bandOf(sr.map(a=>a*a),100/512),ac=std(imuLast(imu.raw[ch],512));
    $('#imuTrem').innerHTML=`Green band = 4–12 Hz. Strongest peak 3–12 Hz: <b>${b.pk.toFixed(1)} Hz</b> · RMS in 4–12 Hz: <b>${b.rms.toFixed(d+1)} ${u}</b> · share of the AC power: <b>${pct(b.rms*b.rms/Math.max(ac*ac,1e-18))}%</b>.`;
  }
}
"""

# ====================================================================== firmware
FW_BLOCK = r"""// ---------------------------------------------------------------- MPU-6050 (optional, I2C 0x68, AD0 -> GND)
// Streams one extra line per sample:  M,seq,t_ms,ax,ay,az,gx,gy,gz,temp,flags*HH   (raw int16 counts)
// +-4 g (8192 LSB/g), +-500 deg/s (65.5 LSB/dps), DLPF 44 Hz (anti-alias for 100 Hz sampling), flags bit0 = read failed.
static const uint8_t MPU_ADDR = 0x68;
static bool imuPresent = false, imuFailed = false;

static bool mpuWrite(uint8_t reg, uint8_t v) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(v);
  return Wire.endTransmission() == 0;
}

static bool mpuInit() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x75);                                   // WHO_AM_I
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom((int)MPU_ADDR, 1) != 1) return false;
  uint8_t id = Wire.read();
  if (id != 0x68 && id != 0x70 && id != 0x71 && id != 0x72 && id != 0x73) return false;
  if (!mpuWrite(0x6B, 0x01)) return false;            // wake, clock = gyro X PLL
  delay(10);
  return mpuWrite(0x1A, 0x03) &&                      // DLPF 44 Hz
         mpuWrite(0x1B, 0x08) &&                      // gyro +-500 dps
         mpuWrite(0x1C, 0x08);                        // accel +-4 g
}

// r[0..2] accel xyz, r[3..5] gyro xyz, r[6] temperature (raw counts)
static bool mpuRead(int16_t* r) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom((int)MPU_ADDR, 14) != 14) return false;
  int16_t w[7];
  for (int i = 0; i < 7; i++) { uint8_t hi = Wire.read(); uint8_t lo = Wire.read(); w[i] = (int16_t)(((uint16_t)hi << 8) | lo); }
  r[0] = w[0]; r[1] = w[1]; r[2] = w[2]; r[3] = w[4]; r[4] = w[5]; r[5] = w[6]; r[6] = w[3];
  return true;
}

"""

def patch(text, old, new, label, count=1):
    if text.count(old) != count:
        sys.exit(f"anchor not found exactly {count}x ({text.count(old)} found): {label}")
    return text.replace(old, new)

def do_html(src):
    h = open(src, encoding="utf-8").read()
    h = patch(h, CSS_OLD, CSS_NEW, "css")
    h = patch(h, HOME_OLD, HOME_NEW, "home button")
    h = patch(h, "</main>", SECTION + "</main>", "end of main")
    h = patch(h, "filt:'bw2',despike:true,", "imuFilt:'bw2',imuFc:15,imuBias:[0,0,0],filt:'bw2',despike:true,", "defaults")
    h = patch(h, "  if(line[0]==='#'){dev.info=line;return}\n", "  if(line[0]==='#'){dev.info=line;return}\n  if(line[0]==='M'){imuLine(line);return}\n", "handleLine")
    h = patch(h, "dev.lastTdev=null;lastRx=0;lastGood=0}", "dev.lastTdev=null;lastRx=0;lastGood=0;imuReset()}", "resetStream")
    h = patch(h, "if(id==='lab')labEnter()}", "if(id==='lab')labEnter();if(id==='imu')imuEnter()}", "show()")
    h = patch(h, "  if($('#lab').classList.contains('on')){drawLab();labButtons()}\n",
              "  if($('#lab').classList.contains('on')){drawLab();labButtons()}\n  if($('#imu').classList.contains('on')){drawImu();imuButtons()}\n", "main loop")
    h = patch(h, "// ---------------------------------------------------------------- HUD (live signals)", JS + "\n// ---------------------------------------------------------------- HUD (live signals)", "js insert")
    return h

def do_fw(src):
    c = open(src, encoding="utf-8").read()
    c = patch(c, "// ---------------------------------------------------------------- setup helpers", FW_BLOCK + "// ---------------------------------------------------------------- setup helpers", "fw block")
    c = patch(c, "static void probeAll(bool atBoot) {\n", "static void probeAll(bool atBoot) {\n  if (atBoot) { imuPresent = mpuInit(); imuFailed = false; }\n", "probeAll")
    c = patch(c, "(unsigned long)maxAcqUs, (unsigned long)overruns, (unsigned long)skipped);\n}",
              "(unsigned long)maxAcqUs, (unsigned long)overruns, (unsigned long)skipped);\n  Serial.printf(\"# imu present=%d failed=%d\\n\", imuPresent, imuFailed);\n}", "status")
    c = patch(c, '  if (!ads[0].present) Serial.println("# WARN ADS1115 0x48 (flex) not found");',
              '  Serial.println("# imu columns: M,seq,t_ms,ax,ay,az,gx,gy,gz,temp,flags*XORHEX (raw counts; 8192 LSB/g, 65.5 LSB/dps)");\n'
              '  if (!imuPresent) Serial.println("# INFO no MPU-6050 at 0x68: no M lines will be sent");\n'
              '  if (!ads[0].present) Serial.println("# WARN ADS1115 0x48 (flex) not found");', "setup info")
    c = patch(c, "  Serial.write((const uint8_t*)line, n);\n  seq++;\n", r"""  Serial.write((const uint8_t*)line, n);

  if (imuPresent) {                       // same seq as the main line so the two streams can be joined offline
    int16_t r[7] = {0, 0, 0, 0, 0, 0, 0};
    uint32_t tm = micros();
    bool ok = !imuFailed && mpuRead(r);
    if (!ok) { imuFailed = true; for (int i = 0; i < 7; i++) r[i] = 0; }
    char ml[120];
    int m = snprintf(ml, sizeof(ml), "M,%lu,%lu.%03lu,%d,%d,%d,%d,%d,%d,%d,%u",
                     (unsigned long)seq, (unsigned long)(tm / 1000UL), (unsigned long)(tm % 1000UL),
                     r[0], r[1], r[2], r[3], r[4], r[5], r[6], ok ? 0u : 1u);
    uint8_t mc = 0;
    for (int i = 0; i < m; i++) mc ^= (uint8_t)ml[i];
    m += snprintf(ml + m, sizeof(ml) - m, "*%02X\n", (unsigned)mc);
    Serial.write((const uint8_t*)ml, m);
  }
  seq++;
""", "loop emit")
    c = patch(c, "    if (needReset) {\n      busReset();\n      probeAll(false);\n    }",
              "    if (imuPresent && imuFailed) needReset = true;\n    if (needReset) {\n      busReset();\n      probeAll(false);\n      if (imuPresent && imuFailed && mpuInit()) imuFailed = false;\n    }", "reprobe")
    return c

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    h, c = sys.argv[1], sys.argv[2]
    oh = os.path.join(os.path.dirname(os.path.abspath(h)), "Game_website_v3.html")
    oc = os.path.join(os.path.dirname(os.path.abspath(c)), "Game_v3.cpp")
    open(oh, "w", encoding="utf-8").write(do_html(h)); print("wrote", oh)
    open(oc, "w", encoding="utf-8").write(do_fw(c)); print("wrote", oc)