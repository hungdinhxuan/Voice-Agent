const stateEl = document.querySelector('#state');
const hintEl = document.querySelector('#hint');
const orb = document.querySelector('#orb');
const connection = document.querySelector('#connection');
const connectionDot = document.querySelector('#connection-dot');
const conversation = document.querySelector('#conversation');
const logs = document.querySelector('#logs');
let socket;
let assistantBubble = null;

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
    if (event.state) setState(event.state);
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
  return `<div id="empty" class="empty"><span class="wave">||||||||</span><p>Hãy nói vào microphone của máy.</p><small>Âm thanh dùng thiết bị trong config.yaml.</small></div>`;
}

document.querySelector('#interrupt').addEventListener('click', () => {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ action: 'interrupt' }));
});
document.querySelector('#clear').addEventListener('click', () => {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ action: 'clear_history' }));
});

connect();

