// Giả lập nửa client của giao thức Xiaozhi, chạy ngay trong trình duyệt.
// Mục tiêu là nhìn thấy được: mỗi frame trên socket đều hiện thành một dòng.

const $ = (id) => document.getElementById(id);

const UPLINK = { rate: 16000, frameMs: 60 };
const UPLINK_SAMPLES = (UPLINK.rate * UPLINK.frameMs) / 1000; // 960
const MAX_ROWS = 400;

const app = {
  ws: null,
  session: '',
  deviceState: 'idle',
  mode: 'auto',
  down: { rate: 24000, frameMs: 60 },
  frames: 0,
  bytes: 0,
  gaps: [],
  lastFrameAt: 0,
  mcpTools: [],
  audioRow: null,
  mic: null,
  encoder: null,
  decoder: null,
  playCtx: null,
  playHead: 0,
};

// ----------------------------------------------------------------- flow log

function row(direction, kind, gloss, payload, extra = '') {
  const flow = $('flow');
  flow.querySelector('.empty-flow')?.remove();
  const el = document.createElement('div');
  el.className = `msg ${direction} ${extra}`;
  const arrow = direction === 'up' ? '↑' : direction === 'down' ? '↓' : '·';
  el.innerHTML = `
    <time>${new Date().toLocaleTimeString('vi-VN', { hour12: false })}.${String(Date.now() % 1000).padStart(3, '0')}</time>
    <span class="arrow">${arrow}</span>
    <div><span class="kind"></span><div class="gloss"></div></div>`;
  el.querySelector('.kind').textContent = kind;
  el.querySelector('.gloss').textContent = gloss;
  if (payload !== undefined) {
    const details = document.createElement('details');
    details.innerHTML = '<summary>JSON</summary>';
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(payload, null, 2);
    details.append(pre);
    el.lastElementChild.append(details);
  }
  flow.append(el);
  while (flow.children.length > MAX_ROWS) flow.firstElementChild.remove();
  flow.scrollTop = flow.scrollHeight;
  return el;
}

function note(text) { row('note', 'ghi chú', text); }
function fail(kind, text) { row('note', kind, text, undefined, 'err'); }

// Gộp frame audio thành một dòng đếm, nếu không màn hình sẽ ngập.
function audioFrame(bytes) {
  const now = performance.now();
  if (app.lastFrameAt) app.gaps.push(now - app.lastFrameAt);
  app.lastFrameAt = now;
  app.frames += 1;
  app.bytes += bytes;

  const median = app.gaps.length
    ? [...app.gaps].sort((a, b) => a - b)[Math.floor(app.gaps.length / 2)]
    : 0;
  $('frames').textContent = String(app.frames);
  $('gap').textContent = median
    ? `${median.toFixed(0)} ms (frame = ${app.down.frameMs} ms)`
    : '—';

  if ($('show-audio').checked) {
    row('down', 'binary', `Opus ${bytes} byte`, undefined, 'audio-row');
    return;
  }
  if (!app.audioRow || !app.audioRow.isConnected) {
    app.audioRow = row('down', 'binary audio', '', undefined, 'audio-row');
    app.audioRowStart = app.frames;
  }
  const count = app.frames - app.audioRowStart + 1;
  app.audioRow.querySelector('.gloss').textContent =
    `${count} gói Opus, nhịp ${median ? median.toFixed(0) : '—'} ms · đây là tiếng nói phát ra loa robot`;
}

// -------------------------------------------------------------- device state

const WHY = {
  idle: 'Thiết bị rảnh. Firmware chỉ mở socket khi có wake word hoặc bấm nút.',
  connecting: 'Đang mở audio channel và chờ hello của server, tối đa 10 giây.',
  listening: 'Đang đẩy Opus từ mic lên. Ở mode auto, server tự quyết định câu kết thúc.',
  speaking: 'Đang nhận audio về. Chỉ ở state này thiết bị mới chịu nhận gói audio.',
};

function setState(next, why) {
  app.deviceState = next;
  for (const el of document.querySelectorAll('.state')) {
    el.classList.toggle('active', el.dataset.state === next);
  }
  $('state-why').textContent = why || WHY[next] || '';
}

function setConn(text, cls) {
  $('conn').textContent = text;
  $('dot').className = cls || '';
}

function enable(connected) {
  for (const id of ['listen-start', 'listen-stop', 'wake', 'abort', 'mode']) {
    $(id).disabled = !connected;
  }
  $('mic').disabled = !connected || !window.AudioEncoder;
  $('connect').textContent = connected ? 'Ngắt kết nối' : 'Kết nối';
  for (const id of ['version', 'feat-mcp', 'feat-aec', 'token']) $(id).disabled = connected;
}

// ------------------------------------------------------------------- socket

function connect() {
  const url = new URL('/xiaozhi/v1/', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  // Trình duyệt không đặt được header WebSocket, nên dùng query param —
  // server chấp nhận cả hai, đúng như backend tham chiếu.
  url.searchParams.set('device-id', 'browser:console');
  url.searchParams.set('client-id', 'xiaozhi-console');
  const token = $('token').value.trim();
  if (token) url.searchParams.set('token', token);

  setConn('Đang kết nối…');
  setState('connecting');
  note(`Mở WebSocket tới ${url.pathname} (device-id và token đi qua query param).`);

  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  app.ws = ws;

  ws.onopen = () => {
    const hello = {
      type: 'hello',
      version: Number($('version').value),
      features: { mcp: $('feat-mcp').checked, ...($('feat-aec').checked ? { aec: true } : {}) },
      transport: 'websocket',
      audio_params: { format: 'opus', sample_rate: UPLINK.rate, channels: 1, frame_duration: UPLINK.frameMs },
    };
    send(hello, 'Khai báo thiết bị. Uplink 16 kHz/60 ms là cố định trong firmware, không thương lượng được.');
  };

  ws.onmessage = (event) => {
    if (typeof event.data !== 'string') { onBinary(event.data); return; }
    let message;
    try { message = JSON.parse(event.data); } catch { fail('lỗi', 'Frame text không phải JSON.'); return; }
    onJson(message);
  };

  ws.onerror = () => fail('lỗi socket', 'Không mở được kết nối. Kiểm tra token và xiaozhi.enabled.');

  ws.onclose = (event) => {
    setConn(`Đã đóng (${event.code})`, event.code === 1000 ? '' : 'error');
    note(`Socket đóng, code ${event.code}. Thiết bị thật sẽ mở lại ở wake word kế tiếp.`);
    setState('idle', 'Socket đã đóng nên thiết bị quay về idle.');
    enable(false);
    stopMic();
    app.ws = null;
  };
}

function send(message, gloss) {
  if (!app.ws || app.ws.readyState !== WebSocket.OPEN) return;
  app.ws.send(JSON.stringify({ session_id: app.session, ...message }));
  row('up', message.type + (message.state ? ` · ${message.state}` : ''), gloss, message);
}

function onJson(message) {
  switch (message.type) {
    case 'hello': {
      app.session = message.session_id || '';
      const params = message.audio_params || {};
      app.down = { rate: params.sample_rate || 24000, frameMs: params.frame_duration || 60 };
      $('down').textContent = `Opus ${app.down.rate} Hz · 1ch · ${app.down.frameMs} ms`;
      $('sid').textContent = app.session || '(server không gửi)';
      $('budget').textContent = `${Math.floor(1200 / app.down.frameMs)} gói ≈ 1.2 s`;
      row('down', 'hello', 'Server chấp nhận. Thiết bị lấy sample rate ở đây để cấu hình bộ giải mã, và mở audio channel.', message);
      setConn('Đã kết nối', 'online');
      setState('listening', 'Nhận được hello nên audio channel mở, firmware vào thẳng listening.');
      enable(true);
      openDecoder();
      break;
    }
    case 'stt':
      row('down', 'stt', 'Kết quả nhận dạng, chỉ để hiện lên màn hình robot.', message);
      break;
    case 'tts':
      if (message.state === 'start') {
        row('down', 'tts · start', 'Lệnh chuyển sang speaking. Từ giờ thiết bị mới chịu nhận audio.', message);
        setState('speaking');
        app.frames = 0; app.bytes = 0; app.gaps = []; app.lastFrameAt = 0; app.audioRow = null;
      } else if (message.state === 'sentence_start') {
        row('down', 'tts · sentence_start', 'Phụ đề cho câu sắp phát.', message);
      } else if (message.state === 'stop') {
        const back = app.mode === 'manual' ? 'idle' : 'listening';
        row('down', 'tts · stop', `Hết lượt. Mode ${app.mode} nên thiết bị về ${back}.`, message);
        setState(back, back === 'listening'
          ? 'Ở mode auto, firmware tự quay lại listening và gửi listen start mới cho lượt sau.'
          : 'Ở mode manual, firmware về idle và chờ bấm nút.');
        if (back === 'listening') {
          setTimeout(() => send(
            { type: 'listen', state: 'start', mode: app.mode },
            'Firmware thật tự gửi lại listen start sau khi phát xong. Trang này bắt chước.',
          ), 250);
        }
      } else {
        row('down', `tts · ${message.state}`, 'State không nằm trong ba giá trị firmware xử lý.', message);
      }
      break;
    case 'mcp':
      handleMcp(message.payload);
      break;
    default:
      row('down', message.type || '(thiếu type)', 'Firmware hiện chưa dùng message này.', message);
  }
}

function onBinary(buffer) {
  audioFrame(buffer.byteLength);
  if (app.deviceState !== 'speaking') {
    fail('bị bỏ', 'Thiết bị thật sẽ vứt gói này vì đang không ở state speaking.');
    return;
  }
  if ($('play').checked) decode(buffer);
}

// --------------------------------------------------------------- BLE bridge

// Web Bluetooth chỉ nói được BLE/GATT. HC-05 và HC-06 là Bluetooth classic SPP
// nên trình duyệt KHÔNG kết nối được — phải dùng HM-10, AT-09, JDY-08 hoặc ESP32.
const UART_PROFILES = {
  nordic: {
    label: 'Nordic UART',
    service: '6e400001-b5a3-f393-e0a9-e50e24dcca9e',
    write: '6e400002-b5a3-f393-e0a9-e50e24dcca9e',
  },
  hm10: { label: 'HM-10 / AT-09', service: 0xffe0, write: 0xffe1 },
};

// Tool robot: tên tool -> dòng lệnh gửi xuống Arduino.
const MOVE_TOOLS = [
  {
    name: 'self.chassis.forward', description: 'Drive the robot forward',
    arg: 'distance_cm', unit: 'distance in centimetres', min: 1, max: 500, fallback: 20,
    command: (args) => `F${args.distance_cm ?? 20}`,
  },
  {
    name: 'self.chassis.backward', description: 'Drive the robot backward',
    arg: 'distance_cm', unit: 'distance in centimetres', min: 1, max: 500, fallback: 20,
    command: (args) => `B${args.distance_cm ?? 20}`,
  },
  {
    name: 'self.chassis.turn_left', description: 'Turn the robot left in place',
    arg: 'degrees', unit: 'angle in degrees', min: 1, max: 360, fallback: 90,
    command: (args) => `L${args.degrees ?? 90}`,
  },
  {
    name: 'self.chassis.turn_right', description: 'Turn the robot right in place',
    arg: 'degrees', unit: 'angle in degrees', min: 1, max: 360, fallback: 90,
    command: (args) => `R${args.degrees ?? 90}`,
  },
  { name: 'self.chassis.stop', description: 'Stop the robot immediately', command: () => 'S' },
];

function moveToolSchema(tool) {
  if (!tool.arg) return { name: tool.name, description: tool.description, inputSchema: { type: 'object', properties: {} } };
  return {
    name: tool.name,
    description: tool.description,
    inputSchema: {
      type: 'object',
      properties: {
        // Có default nên firmware bỏ nó khỏi `required`, và model không bị ép bịa số.
        [tool.arg]: {
          type: 'integer', minimum: tool.min, maximum: tool.max,
          default: tool.fallback, description: tool.unit,
        },
      },
    },
  };
}

const ble = { device: null, characteristic: null };
const usb = { port: null, writer: null };
let commandsSent = 0;

function bleConnected() { return Boolean(ble.characteristic); }
function usbConnected() { return Boolean(usb.writer); }
function robotConnected() { return bleConnected() || usbConnected(); }

// Web Serial: robot cắm USB vào chính máy đang mở trang. Không cần module BLE,
// và sketch nhận cùng bộ lệnh trên cả hai cổng.
async function connectUsb() {
  if (!navigator.serial) {
    throw new Error('Trình duyệt không có Web Serial. Cần Chrome hoặc Edge, và trang phải HTTPS hoặc localhost.');
  }
  const port = await navigator.serial.requestPort();
  await port.open({ baudRate: 115200 });
  usb.port = port;
  usb.writer = port.writable.getWriter();
  readUsb(port);
  setBle(`USB · ${port.getInfo?.().usbProductId ?? 'serial'}`, true);
  note('Đã nối robot qua USB. Arduino reset khi mở cổng, chờ khoảng 2 giây rồi hãy gửi lệnh.');
}

async function readUsb(port) {
  const decoder = new TextDecoderStream();
  port.readable.pipeTo(decoder.writable).catch(() => {});
  const reader = decoder.readable.getReader();
  let pending = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += value;
      let index;
      while ((index = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, index).trim();
        pending = pending.slice(index + 1);
        // Phản hồi thật của robot, thứ mà đường BLE hiện chưa đọc được.
        if (line) row('down', 'robot →', line);
      }
    }
  } catch (error) {
    fail('usb', String(error));
  } finally {
    usb.writer = null; usb.port = null;
    setBle('mất kết nối', false);
  }
}

async function disconnectUsb() {
  try { usb.writer?.releaseLock(); } catch { /* đang đóng */ }
  usb.writer = null;
  await usb.port?.close().catch(() => {});
  usb.port = null;
  setBle('chưa kết nối', false);
}

function setBle(text, ok) {
  $('ble-state').textContent = text;
  $('ble-state').className = `ble-state${ok ? ' ok' : ''}`;
  for (const button of document.querySelectorAll('button[data-cmd]')) button.disabled = !ok;
  $('ble').textContent = ok ? 'Ngắt robot' : 'Kết nối robot (BLE)';
}

function chosenProfiles() {
  const choice = $('ble-profile').value;
  if (choice === 'custom') {
    const [service, write] = $('ble-uuid').value.split(',').map((part) => part.trim());
    if (!service || !write) throw new Error('Cần nhập "service,write" UUID.');
    return [{ label: 'custom', service, write }];
  }
  if (choice === 'auto') return Object.values(UART_PROFILES);
  return [UART_PROFILES[choice]];
}

async function connectBle() {
  if (!navigator.bluetooth) {
    fail('bluetooth', 'Trình duyệt không có Web Bluetooth. Cần Chrome hoặc Edge, và trang phải chạy qua HTTPS.');
    return;
  }
  const profiles = chosenProfiles();
  const device = await navigator.bluetooth.requestDevice({
    acceptAllDevices: true,
    optionalServices: profiles.map((profile) => profile.service),
  });
  const server = await device.gatt.connect();
  let last;
  for (const profile of profiles) {
    try {
      const service = await server.getPrimaryService(profile.service);
      ble.characteristic = await service.getCharacteristic(profile.write);
      ble.device = device;
      device.addEventListener('gattserverdisconnected', () => {
        ble.characteristic = null; ble.device = null;
        setBle('mất kết nối', false);
        note('Robot ngắt BLE. Tool điều khiển sẽ trả lỗi cho LLM cho tới khi kết nối lại.');
      });
      setBle(`BLE · ${device.name || 'robot'} · ${profile.label}`, true);
      note(`Đã nối BLE tới ${device.name || 'thiết bị'} qua ${profile.label}. `
        + 'Các tool chassis giờ đẩy lệnh thật xuống robot.');
      return;
    } catch (error) { last = error; }
  }
  device.gatt.disconnect();
  throw new Error(`Không tìm thấy UART service nào. ${last}`);
}

// Một đường gửi cho cả hai transport. USB được ưu tiên vì chỉ nối được một đường
// tại một thời điểm, và đường USB còn đọc được phản hồi của robot.
async function robotSend(command) {
  const line = new TextEncoder().encode(`${command}\n`);
  let via;
  if (usbConnected()) {
    await usb.writer.write(line);
    via = 'usb';
  } else if (bleConnected()) {
    await ble.characteristic.writeValue(line);
    via = 'ble';
  } else {
    throw new Error('Robot chưa kết nối. Bấm "Kết nối robot (BLE)" hoặc "hoặc qua USB".');
  }
  commandsSent += 1;
  $('ble-last').textContent = command;
  row('up', `${via} →robot`, `Gửi "${command}" xuống AlphaBot2 (lệnh thứ ${commandsSent}).`);
}

// ---------------------------------------------------------------- fake MCP

function buildTools() {
  const tools = [{
    name: 'self.audio_speaker.set_volume',
    description: 'Set the speaker volume of this device',
    inputSchema: {
      type: 'object',
      properties: { volume: { type: 'integer', minimum: 0, maximum: 100 } },
      required: ['volume'],
    },
  }, {
    name: 'self.get_device_status',
    description: 'Get the current status of this device',
    inputSchema: { type: 'object', properties: {} },
  }];
  // Luôn khai tool robot, kể cả khi chưa nối BLE: lúc đó tools/call trả isError
  // để LLM nói lại cho người dùng, thay vì im lặng không làm gì.
  tools.push(...MOVE_TOOLS.map(moveToolSchema));
  if ($('user-tool').checked) {
    tools.push({
      name: 'self.reboot',
      description: 'Reboot the device',
      inputSchema: { type: 'object', properties: {} },
      userOnly: true,
    });
  }
  return tools;
}

function replyMcp(payload, gloss) {
  app.ws?.send(JSON.stringify({ session_id: app.session, type: 'mcp', payload }));
  row('up', 'mcp · reply', gloss, payload);
}

function handleMcp(payload) {
  if (!payload || payload.jsonrpc !== '2.0') return;
  const { method, id } = payload;

  // Firmware bỏ qua mọi method bắt đầu bằng notifications, không trả lời.
  if (typeof method === 'string' && method.startsWith('notifications')) {
    row('down', 'mcp · notification', 'Firmware bỏ qua, không trả lời.', payload);
    return;
  }
  if (typeof id !== 'number') {
    fail('mcp · id sai', 'Firmware đòi id phải là số và sẽ drop im lặng. Đây chính là chỗ dễ sai nhất.');
    return;
  }

  if (method === 'initialize') {
    row('down', 'mcp · initialize', 'Backend bắt tay với MCP server nằm trên robot.', payload);
    replyMcp({
      jsonrpc: '2.0', id,
      result: {
        protocolVersion: '2024-11-05',
        capabilities: { tools: {} },
        serverInfo: { name: 'browser-console', version: '1.0.0' },
      },
    }, 'Thiết bị trả về thông tin MCP server của mình.');
    return;
  }

  if (method === 'tools/list') {
    const withUser = payload.params?.withUserTools === true;
    row('down', 'mcp · tools/list', withUser
      ? 'Backend hỏi CẢ tool user-only.'
      : 'Backend hỏi danh sách tool. Không gửi withUserTools, nên tool user-only bị giấu — có chủ đích.', payload);
    const tools = buildTools().filter((tool) => withUser || !tool.userOnly);
    app.mcpTools = tools;
    replyMcp({
      jsonrpc: '2.0', id,
      result: { tools: tools.map(({ userOnly, ...rest }) => rest) },
      // Trang cuối: firmware bỏ hẳn nextCursor chứ không gửi chuỗi rỗng.
    }, `Trả ${tools.length} tool. Không có nextCursor nghĩa là hết trang.`);
    return;
  }

  if (method === 'tools/call') {
    const name = payload.params?.name;
    row('down', 'mcp · tools/call', `LLM muốn chạy ${name} trên robot.`, payload);
    const tool = app.mcpTools.find((item) => item.name === name);
    if (!tool) {
      replyMcp({ jsonrpc: '2.0', id, error: { code: -32602, message: `Unknown tool: ${name}` } },
        'Tool không tồn tại nên trả lỗi -32602.');
      return;
    }
    const args = payload.params?.arguments || {};
    const move = MOVE_TOOLS.find((item) => item.name === name);
    if (move) { runMove(id, move, args); return; }
    const text = name === 'self.get_device_status'
      ? JSON.stringify({ battery: 87, volume: 60, wifi: 'ok' })
      : `ok: ${JSON.stringify(args)}`;
    replyMcp({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text }], isError: false } },
      'Kết quả quay lại LLM, và LLM nói tiếp dựa trên đó.');
    return;
  }

  replyMcp({ jsonrpc: '2.0', id, error: { code: -32601, message: `Method not implemented: ${method}` } },
    'Method lạ nên trả -32601.');
}

// isError thay vì error JSON-RPC: LLM đọc được nội dung và nói lại cho người dùng,
// thay vì coi đây là lỗi giao thức.
async function runMove(id, move, args) {
  const command = move.command(args);
  try {
    await robotSend(command);
  } catch (error) {
    replyMcp({
      jsonrpc: '2.0', id,
      result: { content: [{ type: 'text', text: `Không gửi được lệnh: ${error.message}` }], isError: true },
    }, 'Robot chưa sẵn sàng. Trả isError để LLM giải thích cho người dùng.');
    return;
  }
  replyMcp({
    jsonrpc: '2.0', id,
    result: { content: [{ type: 'text', text: `sent ${command}` }], isError: false },
  }, `Đã đẩy "${command}" xuống robot.`);
}

// ------------------------------------------------------------------- audio

function openDecoder() {
  if (!window.AudioDecoder || app.decoder) return;
  try {
    app.decoder = new AudioDecoder({
      output: (data) => { play(data); data.close(); },
      error: (error) => fail('decoder', String(error)),
    });
    app.decoder.configure({ codec: 'opus', sampleRate: app.down.rate, numberOfChannels: 1 });
  } catch (error) {
    app.decoder = null;
    note(`Không mở được bộ giải mã Opus: ${error}. Audio vẫn được đếm, chỉ không nghe được.`);
  }
}

function decode(buffer) {
  if (!app.decoder || app.decoder.state !== 'configured') return;
  try {
    app.decoder.decode(new EncodedAudioChunk({
      type: 'key', timestamp: app.frames * app.down.frameMs * 1000, data: buffer,
    }));
  } catch (error) { fail('decoder', String(error)); }
}

function play(data) {
  if (!app.playCtx) app.playCtx = new (window.AudioContext || window.webkitAudioContext)();
  const samples = new Float32Array(data.numberOfFrames);
  data.copyTo(samples, { planeIndex: 0, format: 'f32-planar' });
  const buffer = app.playCtx.createBuffer(1, samples.length, data.sampleRate);
  buffer.copyToChannel(samples, 0);
  const source = app.playCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(app.playCtx.destination);
  const now = app.playCtx.currentTime;
  app.playHead = Math.max(app.playHead, now + 0.05);
  source.start(app.playHead);
  app.playHead += buffer.duration;
}

async function startMic() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'interactive' });
  await ctx.audioWorklet.addModule('/static/mic-processor.js');
  const node = new AudioWorkletNode(ctx, 'pcm-16k-processor', {
    processorOptions: { targetSampleRate: UPLINK.rate, frameSamples: UPLINK_SAMPLES },
  });

  app.encoder = new AudioEncoder({
    output: (chunk) => {
      const bytes = new Uint8Array(chunk.byteLength);
      chunk.copyTo(bytes);
      app.ws?.send(bytes);
    },
    error: (error) => fail('encoder', String(error)),
  });
  app.encoder.configure({
    codec: 'opus', sampleRate: UPLINK.rate, numberOfChannels: 1,
    opus: { frameDuration: UPLINK.frameMs * 1000 },
  });

  let sent = 0;
  node.port.onmessage = (event) => {
    if (!app.ws || app.ws.readyState !== WebSocket.OPEN || app.encoder.state !== 'configured') return;
    const frame = new Float32Array(event.data);
    app.encoder.encode(new AudioData({
      format: 'f32-planar', sampleRate: UPLINK.rate, numberOfFrames: frame.length,
      numberOfChannels: 1, timestamp: (sent * UPLINK.frameMs * 1000), data: frame,
    }));
    sent += 1;
  };
  ctx.createMediaStreamSource(stream).connect(node);
  app.mic = { stream, ctx, node };
  note(`Microphone bật. Mỗi ${UPLINK.frameMs} ms gửi lên một gói Opus ${UPLINK_SAMPLES} sample.`);
}

function stopMic() {
  if (!app.mic) return;
  app.mic.stream.getTracks().forEach((track) => track.stop());
  app.mic.node.port.onmessage = null;
  app.mic.ctx.close();
  app.mic = null;
  if (app.encoder?.state === 'configured') app.encoder.close();
  app.encoder = null;
  $('mic').classList.remove('on');
  $('mic').textContent = 'Bật microphone';
}

// ------------------------------------------------------------------- wiring

$('connect').addEventListener('click', () => {
  if (app.ws) { app.ws.close(1000, 'console'); return; }
  connect();
});

$('listen-start').addEventListener('click', () => {
  app.mode = $('mode').value;
  setState('listening');
  send({ type: 'listen', state: 'start', mode: app.mode },
    app.mode === 'auto'
      ? 'Bắt đầu thu. Ở auto, thiết bị KHÔNG bao giờ gửi listen stop — server tự cắt câu bằng VAD.'
      : 'Bắt đầu thu ở mode ' + app.mode + '.');
});

$('listen-stop').addEventListener('click', () => {
  setState('idle', 'Nhả nút nên thiết bị dừng thu và về idle.');
  send({ type: 'listen', state: 'stop' },
    'Chỉ mode manual mới gửi cái này. Server phải chốt câu ngay tại đây.');
});

$('wake').addEventListener('click', () => {
  send({ type: 'listen', state: 'detect', text: 'Hi XiaoZhi' },
    'Wake word phát hiện tại chỗ. Firmware chỉ gửi khi bật CONFIG_SEND_WAKE_WORD_DATA.');
});

$('abort').addEventListener('click', () => {
  send({ type: 'abort', reason: 'wake_word_detected' },
    'Ngắt lời. Server phải hủy turn, xóa audio đang xếp hàng và xác nhận bằng tts stop.');
});

$('mic').addEventListener('click', async () => {
  if (app.mic) { stopMic(); return; }
  try {
    await startMic();
    $('mic').classList.add('on');
    $('mic').textContent = 'Tắt microphone';
  } catch (error) {
    fail('microphone', String(error));
  }
});

$('clear-log').addEventListener('click', () => { $('flow').innerHTML = ''; app.audioRow = null; });

$('ble-profile').addEventListener('change', (event) => {
  $('custom-row').hidden = event.target.value !== 'custom';
});

$('ble').addEventListener('click', async () => {
  if (bleConnected()) { ble.device?.gatt.disconnect(); return; }
  if (usbConnected()) { await disconnectUsb(); return; }
  try {
    setBle('đang kết nối…', false);
    await connectBle();
  } catch (error) {
    setBle('chưa kết nối', false);
    fail('bluetooth', String(error.message || error));
  }
});

$('usb').addEventListener('click', async () => {
  if (usbConnected()) { await disconnectUsb(); return; }
  if (bleConnected()) { fail('usb', 'Đang nối BLE rồi, ngắt trước đã.'); return; }
  try {
    setBle('đang mở cổng…', false);
    await connectUsb();
  } catch (error) {
    setBle('chưa kết nối', false);
    fail('usb', String(error.message || error));
  }
});

for (const button of document.querySelectorAll('button[data-cmd]')) {
  button.addEventListener('click', () => {
    robotSend(button.dataset.cmd).catch((error) => fail('robot', String(error.message || error)));
  });
}

setBle('chưa kết nối', false);
if (!navigator.bluetooth) $('ble').disabled = true;
if (!navigator.serial) $('usb').disabled = true;
if (!navigator.bluetooth && !navigator.serial) {
  setBle('trình duyệt không hỗ trợ cả Web Bluetooth lẫn Web Serial', false);
}

setState('idle');
enable(false);
if (!window.AudioEncoder) {
  $('mic-note').textContent = 'Trình duyệt này không có WebCodecs nên không mã hóa Opus được. '
    + 'Dùng Chrome hoặc Edge để test bằng giọng nói thật; các phần còn lại vẫn chạy.';
} else {
  $('mic-note').textContent = 'Bấm listen start trước, rồi bật microphone và nói. '
    + 'Cần HTTPS hoặc localhost thì trình duyệt mới cho phép truy cập mic.';
}
