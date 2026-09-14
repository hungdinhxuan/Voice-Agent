// Trang người xem, phục vụ từ flash của ESP32.
//
// Robot không có loa, nên điện thoại của người xem đóng vai loa và màn hình:
// mở http://<ip robot>/ là nghe được câu trả lời và đọc được nội dung.
//
// Giải mã Opus bằng WebCodecs, cùng cách console tại /xiaozhi đang dùng. Trang
// cố tình không có ảnh và không tải gì từ ngoài: robot chỉ phục vụ đúng một file.
#pragma once
#include <pgmspace.h>

const char VIEWER_PAGE[] PROGMEM = R"HTML(<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AlphaBot2</title><style>
:root{--bg:#faf9f7;--fg:#1a1a1a;--dim:#6b6b6b;--line:#e2e0dc;--me:#2f6f4e;--bot:#1f4f82}
@media(prefers-color-scheme:dark){:root{--bg:#15140f;--fg:#f0eee9;--dim:#9a968e;--line:#302e28;--me:#7fd1a6;--bot:#8ab8e8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center;
position:sticky;top:0;background:var(--bg)}
h1{font-size:16px;margin:0;font-weight:600}
#dot{width:9px;height:9px;border-radius:50%;background:#c0392b;flex:none}
#dot.on{background:#2f9e63}
#state{color:var(--dim);font-size:13px;margin-left:auto}
main{padding:16px;max-width:640px;margin:0 auto}
.turn{margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.who{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:3px}
.you .who{color:var(--me)} .bot .who{color:var(--bot)}
.txt{white-space:pre-wrap;word-wrap:break-word}
.act{font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--dim);margin-top:5px}
#empty{color:var(--dim)}
button{font:inherit;padding:9px 14px;border:1px solid var(--line);border-radius:7px;
background:transparent;color:var(--fg);cursor:pointer}
button:disabled{opacity:.45;cursor:default}
</style></head><body>
<header><span id="dot"></span><h1>AlphaBot2</h1>
<button id="snd">Bật tiếng</button><span id="state">đang nối…</span></header>
<main><div id="log"><p id="empty">Chưa có gì. Hãy nói với robot.</p></div></main>
<script>
const $=(i)=>document.getElementById(i);
let dec=null,ctx=null,head=0,rate=24000,frames=0,sound=false,turn=null;

function row(who,label,text){
  $('empty')?.remove();
  const d=document.createElement('div');
  d.className='turn '+who;
  d.innerHTML='<div class="who"></div><div class="txt"></div>';
  d.querySelector('.who').textContent=label;
  d.querySelector('.txt').textContent=text;
  $('log').append(d);
  while($('log').children.length>60)$('log').firstElementChild.remove();
  scrollTo(0,document.body.scrollHeight);
  return d;
}
function act(text){
  const d=document.createElement('div');
  d.className='act'; d.textContent=text;
  ($('log').lastElementChild||row('bot','robot','')).append(d);
  scrollTo(0,document.body.scrollHeight);
}
// Giải mã chỉ mở sau khi người dùng bấm: iOS không cho phát tiếng nếu chưa có thao tác.
function openAudio(){
  if(ctx)return;
  ctx=new (window.AudioContext||window.webkitAudioContext)();
  head=0;
  if(!window.AudioDecoder){act('Trình duyệt không có WebCodecs, chỉ xem được chữ.');return;}
  dec=new AudioDecoder({output:(d)=>{play(d);d.close();},error:(e)=>act('lỗi giải mã: '+e)});
  dec.configure({codec:'opus',sampleRate:rate,numberOfChannels:1});
}
function play(d){
  const s=new Float32Array(d.numberOfFrames);
  d.copyTo(s,{planeIndex:0,format:'f32-planar'});
  const b=ctx.createBuffer(1,s.length,d.sampleRate);
  b.copyToChannel(s,0);
  const src=ctx.createBufferSource();
  src.buffer=b; src.connect(ctx.destination);
  head=Math.max(head,ctx.currentTime+0.08);
  src.start(head); head+=b.duration;
}
$('snd').onclick=()=>{
  sound=!sound;
  if(sound)openAudio();
  if(ctx)ctx.resume();
  $('snd').textContent=sound?'Tắt tiếng':'Bật tiếng';
};

function connect(){
  const ws=new WebSocket('ws://'+location.hostname+':81/');
  ws.binaryType='arraybuffer';
  ws.onopen=()=>{$('dot').className='on';$('state').textContent='đã nối';};
  ws.onclose=()=>{$('dot').className='';$('state').textContent='mất kết nối, đang thử lại';
    setTimeout(connect,2000);};
  ws.onmessage=(e)=>{
    if(typeof e.data!=='string'){
      frames++;
      if(sound&&dec&&dec.state==='configured'){
        try{dec.decode(new EncodedAudioChunk({type:'key',timestamp:frames*60000,
          data:new Uint8Array(e.data)}));}catch(err){}
      }
      return;
    }
    let m; try{m=JSON.parse(e.data);}catch(err){return;}
    if(m.t==='cfg'){rate=m.rate||rate;$('state').textContent=m.mode;return;}
    if(m.t==='mode'){$('state').textContent=m.mode;return;}
    if(m.t==='you'){turn=null;row('you','bạn',m.text);return;}
    if(m.t==='bot'){
      if(turn)turn.querySelector('.txt').textContent+=' '+m.text;
      else turn=row('bot','robot',m.text);
      return;
    }
    if(m.t==='act'){act(m.text);return;}
  };
}
connect();
</script></body></html>)HTML";
