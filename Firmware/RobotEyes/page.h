// The phone app, served BY the robot over HTTP. It uses the ROBOT's mic (over a
// WebSocket on :81) and drives the ROBOT's eyes; the phone is the speaker + the
// bridge to Gemini. Open http://<robot-ip> in Safari.
const char PHONE_PAGE[] PROGMEM = R"HTMLPAGE(<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Ares">
<meta name="theme-color" content="#0b0f17">
<title>Ares</title>
<style>
:root{--bg:#0b0f17;--card:#141b28;--accent:#1a78c2;--accent2:#2fae72;--muted:#8aa0b2;--fg:#e6f3ff;--eye:#16a8e0}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
body{display:flex;flex-direction:column;align-items:center;padding:max(20px,env(safe-area-inset-top)) 20px max(20px,env(safe-area-inset-bottom))}
h1{font-size:20px;font-weight:700;margin:6px 0 2px;letter-spacing:.5px}
.sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.face{display:flex;gap:26px;margin:18px 0 8px;height:120px;align-items:center}
.eye{width:54px;height:96px;border-radius:24px;background:var(--eye);box-shadow:0 0 26px rgba(22,168,224,.55);transition:height .12s,background .3s,box-shadow .3s}
.state-listening .eye{background:var(--accent2);box-shadow:0 0 26px rgba(47,174,114,.6)}
.state-speaking .eye{animation:talk .5s ease-in-out infinite}
@keyframes talk{0%,100%{height:96px}50%{height:78px}}
.status{color:var(--muted);font-size:14px;height:18px;margin-bottom:16px}
.talk{width:100%;max-width:420px;padding:18px;border:0;border-radius:18px;background:var(--accent);color:#eaf6ff;font-size:18px;font-weight:700}
.talk.on{background:#c0392b}
.card{width:100%;max-width:420px;background:var(--card);border-radius:16px;padding:14px;margin-top:14px}
label{display:block;font-size:12px;color:var(--muted);margin:8px 0 4px}
input,select{width:100%;background:#0a0e15;color:var(--fg);border:1px solid #243044;border-radius:10px;padding:11px;font-size:15px}
.save{margin-top:10px;width:100%;padding:11px;border:0;border-radius:10px;background:#27313f;color:var(--fg);font-size:14px}
details{margin-top:14px;width:100%;max-width:420px}summary{color:var(--muted);font-size:14px;padding:8px 0}
#log{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--muted);white-space:pre-wrap;max-height:120px;overflow:auto}
</style></head><body>
<h1>Ares</h1><div class="sub">talking through the robot</div>
<div class="face" id="face"><div class="eye"></div><div class="eye"></div></div>
<div class="status" id="status">tap to start</div>
<button class="talk" id="talk">Start talking</button>
<details><summary>Settings</summary><div class="card">
<label>Gemini API key</label><input id="key" type="password" placeholder="paste your key">
<label>Voice</label><select id="voice"><option>Puck</option><option>Charon</option><option>Kore</option><option>Fenrir</option><option>Aoede</option></select>
<button class="save" id="save">Save</button>
<div class="sub" style="margin-top:8px">Personality loads from the robot.</div>
</div></details>
<details><summary>Log</summary><div class="card"><div id="log"></div></div></details>
<script>
const MODEL="models/gemini-3.1-flash-live-preview";
const $=i=>document.getElementById(i), log=m=>{const l=$("log");l.textContent+=m+"\n";l.scrollTop=l.scrollHeight};
const setStatus=s=>$("status").textContent=s, setFace=s=>$("face").className="face state-"+s;
$("key").value=localStorage.ares_key||""; $("voice").value=localStorage.ares_voice||"Puck";
$("save").onclick=()=>{localStorage.ares_key=$("key").value.trim();localStorage.ares_voice=$("voice").value;log("saved")};
let running=false,wsG=null,wsR=null,ctx=null,nextStart=0,sources=[],speaking=false,wake=null,gReady=false;
$("talk").onclick=()=>running?stop():start();

async function persona(){
  try{const s=await(await fetch("/system.md")).text();const m=await(await fetch("/memory.md")).text();
    return (s||"You are Ares, a warm witty robot companion.")+(m?"\n\n# About your person\n"+m:"");}
  catch{return "You are Ares, a warm, witty robot companion. Keep replies short and spoken.";}
}
async function start(){
  const key=$("key").value.trim()||localStorage.ares_key; if(!key){setStatus("add your Gemini key in Settings");return;}
  localStorage.ares_key=key; gReady=false;
  try{if(navigator.audioSession)navigator.audioSession.type="playback";}catch{}  // iOS: ignore mute switch, use loud speaker
  ctx=new(window.AudioContext||window.webkitAudioContext)(); await ctx.resume();
  try{const pb=ctx.createBuffer(1,1,22050),ps=ctx.createBufferSource();ps.buffer=pb;ps.connect(ctx.destination);ps.start(0);}catch{}  // unlock iOS output on this tap
  const sys=await persona();
  wsR=new WebSocket("ws://"+location.hostname+":81"); wsR.binaryType="arraybuffer";
  wsR.onopen=()=>log("robot mic connected");
  wsR.onmessage=ev=>{ if(typeof ev.data==="string")return;
    if(speaking||!gReady||!wsG||wsG.readyState!==1)return;   // wait for setupComplete
    wsG.send(JSON.stringify({realtimeInput:{audio:{mimeType:"audio/pcm;rate=16000",data:b64(new Uint8Array(ev.data))}}})); };
  wsR.onclose=()=>{ if(running){log("robot mic lost");stop();} };
  const url="wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key="+encodeURIComponent(key);
  wsG=new WebSocket(url); wsG.binaryType="arraybuffer";
  wsG.onopen=()=>{ log("gemini connected"); wsG.send(JSON.stringify({setup:{model:MODEL,
    generationConfig:{responseModalities:["AUDIO"],speechConfig:{voiceConfig:{prebuiltVoiceConfig:{voiceName:$("voice").value}}}},
    systemInstruction:{parts:[{text:sys}]},tools:[{googleSearch:{}}]}})); };
  wsG.onmessage=onGemini; wsG.onclose=e=>{log("gemini closed "+e.code);if(running)stop();};
  wsG.onerror=()=>setStatus("connection error");
  running=true; $("talk").textContent="Stop"; $("talk").classList.add("on"); setStatus("connecting…"); lockScreen();
}
async function onGemini(ev){
  let t=ev.data; if(t instanceof Blob)t=await t.text(); else if(t instanceof ArrayBuffer)t=new TextDecoder().decode(t);
  let m; try{m=JSON.parse(t)}catch{return}
  if(m.setupComplete){gReady=true;log("ready — talk to the robot");setEyes("listening");setStatus("listening");return;}
  const sc=m.serverContent; if(!sc)return;
  if(sc.interrupted){flush();setEyes("listening");}
  const parts=sc.modelTurn&&sc.modelTurn.parts||[];
  for(const p of parts) if(p.inlineData&&p.inlineData.data) playPCM(p.inlineData.data);
}
function b64(u8){let s="";for(let i=0;i<u8.length;i++)s+=String.fromCharCode(u8[i]);return btoa(s);}
function playPCM(b){const bin=atob(b),u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);
  const i16=new Int16Array(u.buffer),f=new Float32Array(i16.length);for(let i=0;i<i16.length;i++)f[i]=i16[i]/32768;
  const buf=ctx.createBuffer(1,f.length,24000);buf.copyToChannel(f,0);
  const s=ctx.createBufferSource();s.buffer=buf;s.connect(ctx.destination);
  const at=Math.max(ctx.currentTime,nextStart);s.start(at);nextStart=at+buf.duration;
  speaking=true;setEyes("speaking");setStatus("speaking…");sources.push(s);
  s.onended=()=>{sources=sources.filter(x=>x!==s);if(nextStart<=ctx.currentTime+0.05){speaking=false;if(running){setEyes("listening");setStatus("listening");}}};
}
function flush(){sources.forEach(s=>{try{s.stop()}catch{}});sources=[];nextStart=0;speaking=false;}
function setEyes(s){setFace(s);if(wsR&&wsR.readyState===1)wsR.send(JSON.stringify({cmd:"state",value:s}));}
function stop(){running=false;$("talk").textContent="Start talking";$("talk").classList.remove("on");setFace("idle");setStatus("tap to start");
  flush();unlock();try{wsR&&wsR.send(JSON.stringify({cmd:"state",value:"idle"}))}catch{}
  try{wsG&&wsG.close()}catch{}try{wsR&&wsR.close()}catch{}try{ctx&&ctx.close()}catch{}wsG=wsR=ctx=null;}
async function lockScreen(){try{if("wakeLock"in navigator)wake=await navigator.wakeLock.request("screen")}catch{}}
function unlock(){try{wake&&wake.release()}catch{}wake=null;}
document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible"&&running)lockScreen();});
setFace("idle");
</script></body></html>)HTMLPAGE";
