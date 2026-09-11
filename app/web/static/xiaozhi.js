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

setState('idle');
enable(false);
if (!window.AudioEncoder) {
  $('mic-note').textContent = 'Trình duyệt này không có WebCodecs nên không mã hóa Opus được. '
    + 'Dùng Chrome hoặc Edge để test bằng giọng nói thật; các phần còn lại vẫn chạy.';
} else {
  $('mic-note').textContent = 'Bấm listen start trước, rồi bật microphone và nói. '
    + 'Cần HTTPS hoặc localhost thì trình duyệt mới cho phép truy cập mic.';
}
