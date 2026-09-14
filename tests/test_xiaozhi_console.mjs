// Chạy logic của app/web/static/xiaozhi.js trong một DOM tối giản.
//
// Đây là phần duy nhất của console mà pytest không chạm tới được: xử lý message
// của giao thức, và ô "Điều khiển robot" có gắn đúng tham số vào URL không.
//
//   node tests/test_xiaozhi_console.mjs        (không cần cài gì ngoài node)
//
// XZ_JS=<file> trỏ sang một bản xiaozhi.js khác, dùng để kiểm bộ test không rỗng.
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const ids = new Set([...read('app/web/static/xiaozhi.html')
  .matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));

const allNodes = [];
const make = (id) => track({
  id, textContent: '', value: id === 'version' ? '1' : (id === 'mode' ? 'auto' : ''),
  checked: id === 'feat-mcp' || id === 'play', disabled: false, className: '', isConnected: true,
  children: [], dataset: {}, scrollTop: 0, scrollHeight: 0,
  classList: { add() {}, remove() {}, toggle() {} },
  addEventListener(t, fn) { (this._h ||= {})[t] = fn; },
  append(...k) { this.children.push(...k); }, remove() {},
  querySelector(s) { return make('q' + s); }, querySelectorAll() { return []; },
  get firstElementChild() { return this.children[0] ?? null; },
  get lastElementChild() { return this.children[this.children.length - 1] ?? make('last'); },
  set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html ?? ''; },
});
function track(node) { allNodes.push(node); return node; }

const nodes = new Map([...ids].map((id) => [id, make(id)]));

const sent = [];
const opened = [];
function FakeWS(url) {
  opened.push(String(url));
  this.readyState = 1;
  this.send = (m) => sent.push(m);
  this.close = () => {};
}
FakeWS.OPEN = 1;

const sandbox = {
  document: {
    getElementById: (id) => nodes.get(id) ?? null,
    querySelectorAll: () => [],
    createElement: (t) => make(t),
  },
  console,
  location: { href: 'https://voiceagent.example/xiaozhi', protocol: 'https:' },
  navigator: {},
  performance, Date, JSON, Math, Number, String, Array, Object, URL, Promise, Error,
  TextEncoder, TextDecoder, setTimeout, clearTimeout,
  AudioContext: function () {}, WebSocket: FakeWS,
};
sandbox.window = sandbox;
const ctx = vm.createContext(sandbox);
vm.runInContext(
  process.env.XZ_JS ? fs.readFileSync(process.env.XZ_JS, 'utf8') : read('app/web/static/xiaozhi.js'),
  ctx,
  { filename: 'xiaozhi.js' },
);

const run = (code) => vm.runInContext(code, ctx);
const fails = [];
const check = (label, cond, extra = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${extra ? '  ' + extra : ''}`);
  if (!cond) fails.push(label);
};
const lastPayload = () => JSON.parse(sent.at(-1)).payload;

// --- ô "Điều khiển robot" gắn tham số vào URL -------------------------------

nodes.get('control').value = 'alphabot2';
run('connect()');
check('control=alphabot2 đi vào URL WebSocket',
  opened.at(-1).includes('control=alphabot2'), opened.at(-1));

nodes.get('control').value = '';
run('connect()');
check('để trống thì không gắn control', !opened.at(-1).includes('control='), opened.at(-1));

// --- MCP ---------------------------------------------------------------------

run("app.session='s1'; app.ws=new WebSocket('wss://x/');");
run(`handleMcp({jsonrpc:'2.0',id:1,method:'initialize',params:{}})`);
check('initialize được trả lời', lastPayload().result?.serverInfo !== undefined);

run(`handleMcp({jsonrpc:'2.0',id:2,method:'tools/list'})`);
const tools = run('app.mcpTools.map(t=>t.name)');
check('không còn tool chassis nào được khai',
  !tools.some((n) => n.startsWith('self.chassis')), tools.join(', '));
check('vẫn còn hai tool minh hoạ giao thức',
  tools.includes('self.audio_speaker.set_volume') && tools.includes('self.get_device_status'));

run(`handleMcp({jsonrpc:'2.0',id:3,method:'tools/call',params:{name:'self.get_device_status',arguments:{}}})`);
check('get_device_status trả nội dung, không lỗi', lastPayload().result?.isError === false);

run(`handleMcp({jsonrpc:'2.0',id:4,method:'tools/call',params:{name:'self.chassis.forward',arguments:{}}})`);
check('gọi tool chassis giờ là -32602', lastPayload().error?.code === -32602,
  JSON.stringify(lastPayload().error?.message));

const before = sent.length;
run(`handleMcp({jsonrpc:'2.0',method:'notifications/state_changed',params:{}})`);
check('notification không được trả lời', sent.length === before);

// --- message của server không được làm nổ trang ------------------------------

for (const [label, code] of [
  ['server hello', `onJson({type:'hello',transport:'websocket',session_id:'abc',audio_params:{format:'opus',sample_rate:24000,channels:1,frame_duration:60}})`],
  ['stt', `onJson({type:'stt',text:'xin chào'})`],
  ['tts start', `onJson({type:'tts',state:'start'})`],
  ['tts sentence_start', `onJson({type:'tts',state:'sentence_start',text:'Chào bạn.'})`],
  ['frame nhị phân', `onBinary(new ArrayBuffer(180))`],
  ['tts stop', `onJson({type:'tts',state:'stop'})`],
  ['message type lạ', `onJson({type:'alert',status:'x',message:'y'})`],
  ['message thiếu type', `onJson({})`],
]) {
  let ok = true;
  try { run(code); } catch (e) { ok = false; console.log('   ', e.message); }
  check(label, ok);
}

console.log(fails.length ? `\n${fails.length} FAIL` : '\nTất cả đều pass');
process.exit(fails.length ? 1 : 0);
