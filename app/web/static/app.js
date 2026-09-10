const stateEl = document.querySelector('#state');
const hintEl = document.querySelector('#hint');
const orb = document.querySelector('#orb');
const connection = document.querySelector('#connection');
const connectionDot = document.querySelector('#connection-dot');
const conversation = document.querySelector('#conversation');
const logs = document.querySelector('#logs');
const micButton = document.querySelector('#microphone');
const micStatus = document.querySelector('#microphone-status');
let socket;
let assistantBubble = null;
let ownsAudio = false;
let microphoneStream = null;
let captureContext = null;
let captureNode = null;
let playbackContext = null;
let playbackTurn = null;
let playbackCursor = 0;
let playbackEndTimer = null;
const playbackSources = new Set();
const playbackStarted = new Set();

const stateLabels = {
  IDLE: ['Sẵn sàng', 'Hãy nói vào microphone.'],
  LISTENING: ['Đang nghe', 'Tiếp tục nói, tôi đang lắng nghe.'],
  PROCESSING: ['Đang suy nghĩ', 'Nhận dạng và tạo phản hồi.'],
  SPEAKING: ['Đang trả lời', 'Bạn có thể nói để ngắt.'],
  INTERRUPTED: ['Đã ngắt', 'Đang chuyển sang lượt mới.'],
};

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/ws`);
  socket.onopen = () => setConnection(true);
  socket.onclose = () => {
    setConnection(false);
    stopMicrophone();
    clearBrowserAudio();
    setTimeout(connect, 1500);
  };
  socket.onmessage = ({ data }) => handleEvent(JSON.parse(data));
}

function setConnection(online) {
  connection.textContent = online ? 'Đã kết nối' : 'Mất kết nối';
  connectionDot.classList.toggle('online', online);
}

function handleEvent(event) {
  if (event.type === 'hello' || event.type === 'ready') {
    document.querySelector('#backend').textContent = event.backend;
    document.querySelector('#model').textContent = event.model;
    if (event.models) renderModels(event.models);
    if (event.state) setState(event.state);
    if (event.type === 'hello') configureAudioOwner(event.audio_owner);
  } else if (event.type === 'state') {
    setState(event.state);
  } else if (event.type === 'transcript') {
    addMessage('user', event.text);
    assistantBubble = null;
  } else if (event.type === 'assistant_delta') {
    if (!assistantBubble) assistantBubble = addMessage('assistant', '');
    assistantBubble.querySelector('.message-text').textContent += event.text;
    scrollConversation();
  } else if (event.type === 'assistant_done') {
    assistantBubble = null;
  } else if (event.type === 'audio_chunk' && ownsAudio) {
    playAudioChunk(event);
  } else if (event.type === 'audio_end' && ownsAudio) {
    finishAudioTurn(event.turn_id);
  } else if (event.type === 'audio_clear' && ownsAudio) {
    clearBrowserAudio();
  } else if (event.type === 'metric') {
    const target = document.querySelector(`#metric-${event.name}`);
    if (target) target.textContent = `${event.value} ${event.unit}`;
  } else if (event.type === 'history_cleared') {
    conversation.innerHTML = emptyMarkup();
    assistantBubble = null;
  } else if (event.type === 'fatal') {
    setState('ERROR');
    hintEl.textContent = event.message;
    appendLog(event.message, 'error');
  }
  if (event.type === 'log') appendLog(event.message, event.level);
}

function configureAudioOwner(isOwner) {
  ownsAudio = isOwner === true;
  micButton.disabled = !ownsAudio;
  micStatus.textContent = ownsAudio
    ? 'Microphone và loa dùng trên trình duyệt này.'
    : 'Tab khác đang giữ quyền audio.';
}

async function startMicrophone() {
  if (!ownsAudio || captureContext) return;
  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    captureContext = new AudioContextClass({ latencyHint: 'interactive' });
    await captureContext.audioWorklet.addModule('/static/mic-processor.js');
    const source = captureContext.createMediaStreamSource(microphoneStream);
    captureNode = new AudioWorkletNode(captureContext, 'pcm-16k-processor', {
      processorOptions: { targetSampleRate: 16000, frameSamples: 512 },
    });
    const silent = captureContext.createGain();
    silent.gain.value = 0;
    captureNode.port.onmessage = ({ data }) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(data);
    };
    source.connect(captureNode);
    captureNode.connect(silent);
    silent.connect(captureContext.destination);
    await captureContext.resume();
    await ensurePlaybackContext();
    micButton.textContent = 'Tắt microphone';
    micButton.classList.add('active');
    micStatus.textContent = 'Đang dùng microphone và loa của trình duyệt.';
  } catch (error) {
    stopMicrophone();
    micStatus.textContent = `Không mở được microphone: ${error.message}`;
    appendLog(`Browser audio error: ${error.message}`, 'error');
  }
}

function stopMicrophone() {
  captureNode?.disconnect();
  captureNode = null;
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = null;
  captureContext?.close();
  captureContext = null;
  micButton.textContent = 'Bật microphone';
  micButton.classList.remove('active');
  if (ownsAudio) micStatus.textContent = 'Microphone đang tắt.';
}

async function ensurePlaybackContext() {
  if (!playbackContext) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    playbackContext = new AudioContextClass({ latencyHint: 'interactive' });
  }
  if (playbackContext.state === 'suspended') await playbackContext.resume();
}

function playAudioChunk(event) {
  if (!playbackContext || playbackContext.state !== 'running') {
    appendLog('Bật microphone để cho phép trình duyệt phát audio.', 'error');
    return;
  }
  if (playbackTurn !== event.turn_id) {
    clearBrowserAudio();
    playbackTurn = event.turn_id;
    playbackCursor = playbackContext.currentTime + 0.04;
  }
  const binary = atob(event.pcm);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const samples = new Float32Array(bytes.buffer);
  const buffer = playbackContext.createBuffer(1, samples.length, event.sample_rate);
  buffer.copyToChannel(samples, 0);
  const source = playbackContext.createBufferSource();
  source.buffer = buffer;
  source.connect(playbackContext.destination);
  const startsAt = Math.max(playbackCursor, playbackContext.currentTime + 0.01);
  source.start(startsAt);
  playbackCursor = startsAt + buffer.duration;
  playbackSources.add(source);
  source.onended = () => playbackSources.delete(source);
  if (!playbackStarted.has(event.turn_id)) {
    playbackStarted.add(event.turn_id);
    sendAction('audio_started', { turn_id: event.turn_id });
  }
}

function finishAudioTurn(turnId) {
  if (turnId !== playbackTurn || !playbackContext) return;
  const delay = Math.max(0, (playbackCursor - playbackContext.currentTime) * 1000);
  clearTimeout(playbackEndTimer);
  playbackEndTimer = setTimeout(() => {
    sendAction('audio_drained', { turn_id: turnId });
    playbackStarted.delete(turnId);
    playbackTurn = null;
    playbackEndTimer = null;
  }, delay + 20);
}

function clearBrowserAudio() {
  if (playbackTurn !== null) playbackStarted.delete(playbackTurn);
  clearTimeout(playbackEndTimer);
  playbackEndTimer = null;
  for (const source of playbackSources) {
    try { source.stop(); } catch (_) { /* already stopped */ }
  }
  playbackSources.clear();
  playbackTurn = null;
  playbackCursor = playbackContext?.currentTime || 0;
}

function sendAction(action, data = {}) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ action, ...data }));
  }
}

function renderModels(models) {
  const container = document.querySelector('#models');
  container.replaceChildren();
  for (const [kind, details] of Object.entries(models)) {
    const card = document.createElement('article');
    card.className = 'model-card';
    const heading = document.createElement('div');
    heading.className = 'model-card-head';
    const badge = document.createElement('span');
    badge.textContent = kind.toUpperCase();
    const title = document.createElement('strong');
    title.textContent = details.model;
    heading.append(badge, title);
    const fields = document.createElement('dl');
    for (const [key, value] of Object.entries(details)) {
      if (key === 'model' || value === null || value === undefined) continue;
      const term = document.createElement('dt');
      term.textContent = key.replaceAll('_', ' ');
      const description = document.createElement('dd');
      description.textContent = String(value);
      fields.append(term, description);
    }
    card.append(heading, fields);
    container.append(card);
  }
}

function setState(state) {
  const [label, hint] = stateLabels[state] || [state, 'Kiểm tra nhật ký hệ thống.'];
  stateEl.textContent = label;
  hintEl.textContent = hint;
  orb.className = `orb ${state.toLowerCase()}`;
}

function addMessage(role, text) {
  document.querySelector('#empty')?.remove();
  const item = document.createElement('article');
  item.className = `message ${role}`;
  const roleLabel = role === 'user' ? 'Bạn' : 'Trợ lý';
  item.innerHTML = `<span>${roleLabel}</span><p class="message-text"></p>`;
  item.querySelector('.message-text').textContent = text;
  conversation.appendChild(item);
  scrollConversation();
  return item;
}

function scrollConversation() {
  conversation.scrollTop = conversation.scrollHeight;
}

function appendLog(message, level = 'info') {
  const time = new Date().toLocaleTimeString('vi-VN', { hour12: false });
  logs.textContent += `${time} ${level === 'error' ? '[ERROR] ' : ''}${message}\n`;
  const lines = logs.textContent.split('\n');
  if (lines.length > 160) logs.textContent = lines.slice(-160).join('\n');
  logs.scrollTop = logs.scrollHeight;
}

function emptyMarkup() {
  return `<div id="empty" class="empty"><span class="wave">||||||||</span><p>Hãy bật microphone và nói.</p><small>Âm thanh được thu và phát trên trình duyệt này.</small></div>`;
}

micButton.addEventListener('click', () => {
  if (captureContext) stopMicrophone();
  else startMicrophone();
});
document.querySelector('#interrupt').addEventListener('click', () => {
  sendAction('interrupt');
});
document.querySelector('#clear').addEventListener('click', () => {
  sendAction('clear_history');
});

connect();
