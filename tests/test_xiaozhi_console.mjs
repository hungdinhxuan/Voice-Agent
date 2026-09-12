// Chạy xiaozhi.js với Web Serial giả, để chứng minh đường USB thật sự đẩy được
// lệnh xuống robot khi LLM gọi tools/call — thứ mà trình duyệt mới kiểm được.
// Chạy logic của app/web/static/xiaozhi.js với một cổng Web Serial giả, để chứng
// minh đường USB thật sự đẩy được lệnh xuống robot khi LLM gọi tools/call. Đây là
// phần duy nhất của console mà pytest không chạm tới được.
//
//   node tests/test_xiaozhi_console.mjs        (không cần cài gì ngoài node)
//
// XZ_JS=<file> trỏ sang một bản xiaozhi.js khác, dùng để kiểm tra bộ test không rỗng.
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

// --- cổng serial giả: ghi vào written[], đọc ra từ inbox ---------------------
const written = [];
let pushLine;
const fakePort = {
  opened: null,
  async open(opts) { this.opened = opts; },
  get writable() {
    return { getWriter: () => ({
      write: async (bytes) => { written.push(new TextDecoder().decode(bytes)); },
      releaseLock() {},
    }) };
  },
  get readable() {
    return new ReadableStream({ start(c) { pushLine = (s) => c.enqueue(new TextEncoder().encode(s)); } });
  },
  getInfo() { return { usbVendorId: 0x2341, usbProductId: 0x0043 }; },
  async close() {},
};

const sent = [];
function FakeWS() { this.readyState = 1; this.send = (m) => sent.push(m); this.close = () => {}; }
FakeWS.OPEN = 1;

const sandbox = {
  document: { getElementById: (id) => nodes.get(id) ?? null, querySelectorAll: () => [], createElement: (t) => make(t) },
  console, location: { href: 'https://x/xiaozhi', protocol: 'https:' },
  navigator: { serial: { requestPort: async () => fakePort } },
  performance, Date, JSON, Math, Number, String, Array, Object, URL, Promise, Error,
  TextEncoder, TextDecoder, TextDecoderStream, ReadableStream,
  setTimeout, clearTimeout, AudioContext: function () {}, WebSocket: FakeWS,
};
sandbox.window = sandbox;
const ctx = vm.createContext(sandbox);
vm.runInContext((process.env.XZ_JS ? fs.readFileSync(process.env.XZ_JS, 'utf8') : read('app/web/static/xiaozhi.js')), ctx, { filename: 'xiaozhi.js' });

const run = (code) => vm.runInContext(code, ctx);
const fails = [];
const check = (label, cond, extra = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${extra ? '  ' + extra : ''}`);
  if (!cond) fails.push(label);
};

// 1. chưa nối -> tool phải trả isError, không được ném ra ngoài
run(`app.session='s1'; app.ws=new WebSocket();`);
// Backend thật luôn initialize rồi tools/list trước khi gọi tool.
await run(`handleMcp({jsonrpc:'2.0',id:90,method:'initialize',params:{}})`);
await run(`handleMcp({jsonrpc:'2.0',id:91,method:'tools/list'})`);
await new Promise((r) => setTimeout(r, 20));
check('tools/list khai đủ 5 tool chassis',
  run("app.mcpTools.filter(t=>t.name.startsWith('self.chassis')).length") === 5,
  run("app.mcpTools.map(t=>t.name).join(',')"));
await run(`handleMcp({jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'self.chassis.forward',arguments:{distance_cm:20}}})`);
await new Promise((r) => setTimeout(r, 30));
let last = sent.length ? JSON.parse(sent.at(-1)).payload : {};
check('chưa nối: trả isError cho LLM', last.result?.isError === true, JSON.stringify(last.result?.content?.[0]?.text));
check('chưa nối: không ghi byte nào', written.length === 0);

// 2. nối USB — sketch thật in "ready" ngay sau khi boot
const connecting = run('connectUsb()');
await new Promise((r) => setTimeout(r, 30));
pushLine('ready\n');
await connecting;
check('connectUsb: bắt được dòng boot của robot',
  allNodes.some((n) => String(n.textContent ?? '').includes('Robot đã boot')));
check('connectUsb: cổng mở', fakePort.opened !== null, JSON.stringify(fakePort.opened));
check('connectUsb: usbConnected()', run('usbConnected()') === true);

// 3. tool call -> phải ra đúng "F20\n"
await run(`handleMcp({jsonrpc:'2.0',id:2,method:'tools/call',params:{name:'self.chassis.forward',arguments:{distance_cm:20}}})`);
await new Promise((r) => setTimeout(r, 30));
check('forward(20) ghi "F20\n" xuống cổng', written.at(-1) === 'F20\n', JSON.stringify(written.at(-1)));
last = JSON.parse(sent.at(-1)).payload;
check('forward(20): isError false', last.result?.isError === false, JSON.stringify(last.result?.content?.[0]?.text));

// 4. không tham số -> phải dùng default, không được ra "Fundefined"
await run(`handleMcp({jsonrpc:'2.0',id:3,method:'tools/call',params:{name:'self.chassis.forward',arguments:{}}})`);
await new Promise((r) => setTimeout(r, 30));
check('forward() không tham số -> F20', written.at(-1) === 'F20\n', JSON.stringify(written.at(-1)));

// 5. cả bốn hướng + stop
for (const [name, args, want] of [
  ['self.chassis.backward', { distance_cm: 30 }, 'B30\n'],
  ['self.chassis.turn_left', { degrees: 90 }, 'L90\n'],
  ['self.chassis.turn_right', { degrees: 45 }, 'R45\n'],
  ['self.chassis.stop', {}, 'S\n'],
]) {
  await run(`handleMcp({jsonrpc:'2.0',id:9,method:'tools/call',params:{name:'${name}',arguments:${JSON.stringify(args)}}})`);
  await new Promise((r) => setTimeout(r, 20));
  check(`${name} -> ${JSON.stringify(want)}`, written.at(-1) === want, JSON.stringify(written.at(-1)));
}

// 6. phản hồi robot phải hiện trong luồng message
pushLine('ok F20\nerr unknown command\n');
await new Promise((r) => setTimeout(r, 60));
const flow = allNodes.map((n) => String(n.textContent ?? '')).join(' | ');
check('phản hồi "ok F20" hiện trong log', flow.includes('ok F20'));
check('phản hồi "err unknown command" hiện trong log', flow.includes('err unknown command'));

// 7. cong treo khi ghi -> phai bao loi, khong duoc treo tools/call mai
run('usb.writer = { write: () => new Promise(() => {}), releaseLock() {} }');
const t0 = Date.now();
await run(`handleMcp({jsonrpc:'2.0',id:20,method:'tools/call',params:{name:'self.chassis.forward',arguments:{distance_cm:5}}})`);
const before = sent.length;
while (sent.length === before && Date.now() - t0 < 8000) await new Promise((r) => setTimeout(r, 50));
const elapsed = Date.now() - t0;
const stuck = JSON.parse(sent.at(-1)).payload;
check('ghi treo -> tra isError thay vi treo mai', stuck.result?.isError === true,
  JSON.stringify(stuck.result?.content?.[0]?.text));
check('ghi treo -> bo cuoc duoi 5 giay', elapsed < 5000, elapsed + 'ms');

console.log(fails.length ? `\n${fails.length} FAIL` : '\nTất cả đều pass');
process.exit(fails.length ? 1 : 0);
