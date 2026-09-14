"""Gia lap con robot: gui dung nhung gi firmware gui, khong can phan cung.

Moi message JSON duoi day duoc chep tu remote.h, va audio di len la PCM tho
16-bit 16 kHz dung nhu pumpUplink() day len. Chay duoc script nay nghia la
giao thuc khop voi adapter - phan con lai chi la C++ co chay dung khong.

Dung khi sua giao thuc trong remote.h: sua o day truoc, chay, thay xanh roi
moi sua firmware theo.

  uv run --with soundfile --with websockets python       hardware/alphabot2_esp32/sim_device.py ws://127.0.0.1:8080 loi-noi.wav

File wav phai la mono 16 kHz PCM_16. Thoat 0 neu co lenh xuong banh xe.
"""
import asyncio, json, sys, time
from pathlib import Path

import soundfile as sf
import websockets

BASE = sys.argv[1].rstrip("/")
WAV = Path(sys.argv[2])
DEVICE = "alphabot2"

FRAME = 960              # 60 ms @ 16 kHz, giong UPLINK_SAMPLES

# Chep tu TOOLS_JSON trong remote.h.
TOOLS_JSON = [
    {"name": "self.chassis.forward", "description": "Drive the robot forward",
     "inputSchema": {"type": "object", "properties": {"distance_cm": {
         "type": "integer", "minimum": 1, "maximum": 500, "default": 20,
         "description": "distance in centimetres"}}}},
    {"name": "self.chassis.backward", "description": "Drive the robot backward",
     "inputSchema": {"type": "object", "properties": {"distance_cm": {
         "type": "integer", "minimum": 1, "maximum": 500, "default": 20,
         "description": "distance in centimetres"}}}},
    {"name": "self.chassis.turn_left", "description": "Turn the robot left in place",
     "inputSchema": {"type": "object", "properties": {"degrees": {
         "type": "integer", "minimum": 1, "maximum": 360, "default": 90,
         "description": "angle in degrees"}}}},
    {"name": "self.chassis.turn_right", "description": "Turn the robot right in place",
     "inputSchema": {"type": "object", "properties": {"degrees": {
         "type": "integer", "minimum": 1, "maximum": 360, "default": 90,
         "description": "angle in degrees"}}}},
    {"name": "self.chassis.stop", "description": "Stop the robot immediately",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "self.get_device_status", "description": "Get the current status of this device",
     "inputSchema": {"type": "object", "properties": {}}},
]

VERB = {"self.chassis.forward": ("F", "distance_cm", 20),
        "self.chassis.backward": ("B", "distance_cm", 20),
        "self.chassis.turn_left": ("L", "degrees", 90),
        "self.chassis.turn_right": ("R", "degrees", 90),
        "self.chassis.stop": ("S", None, 0)}


async def main():
    url = f"{BASE}/xiaozhi/v1/?device-id={DEVICE}&client-id={DEVICE}"
    audio, rate = sf.read(WAV, dtype="int16")
    assert rate == 16000, rate

    session = ""
    uplink_open = False
    commands = []
    replies = []
    tool_list_sent = False

    async with websockets.connect(url, open_timeout=15, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "hello", "version": 1, "transport": "websocket",
            "features": {"mcp": True},
            "audio_params": {"format": "pcm", "sample_rate": 16000,
                             "channels": 1, "frame_duration": 60},
        }))

        async def pump():
            """Day tung khung 60 ms theo thoi gian thuc, nhu mic that."""
            nonlocal uplink_open
            while not uplink_open:
                await asyncio.sleep(0.02)
            start = time.monotonic()
            for i in range(0, len(audio) - FRAME, FRAME):
                if not uplink_open:
                    await asyncio.sleep(0.05)
                    continue
                await ws.send(audio[i:i + FRAME].tobytes())
                target = start + (i / FRAME + 1) * 0.06
                await asyncio.sleep(max(0, target - time.monotonic()))
            print("  [sim] het file audio")

        pump_task = None
        done_speaking = False          # da thay tts stop chua
        deadline = time.monotonic() + 90
        quiet_until = None
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                # Het audio roi van phai doi ASR + LLM + TTS chay xong. Chi dung
                # khi da nghe tra loi, hoac im lang qua 20 giay sau do.
                if done_speaking:
                    break
                if pump_task and pump_task.done():
                    if quiet_until is None:
                        quiet_until = time.monotonic() + 20
                    elif time.monotonic() > quiet_until:
                        break
                continue
            if isinstance(raw, bytes):
                continue                      # TTS Opus, firmware chuyen tiep sang trinh duyet
            msg = json.loads(raw)
            kind = msg.get("type")

            if kind == "hello":
                session = msg.get("session_id", "")
                print(f"  hello ok: session={session[:8]} downlink={msg['audio_params']['sample_rate']}")
                await ws.send(json.dumps({"session_id": session, "type": "listen",
                                          "state": "start", "mode": "auto"}))
                uplink_open = True
                pump_task = asyncio.create_task(pump())

            elif kind == "stt":
                print(f"  stt -> {msg.get('text')!r}")

            elif kind == "tts":
                state = msg.get("state")
                if state == "start":
                    uplink_open = False
                elif state == "sentence_start":
                    replies.append(msg.get("text", ""))
                    print(f"  tts  -> {msg.get('text')!r}")
                elif state == "stop":
                    done_speaking = True
                    uplink_open = True
                    await ws.send(json.dumps({"session_id": session, "type": "listen",
                                              "state": "start", "mode": "auto"}))

            elif kind == "mcp":
                p = msg["payload"]
                method, mid = p.get("method"), p.get("id")
                if method == "initialize":
                    await ws.send(json.dumps({"session_id": session, "type": "mcp", "payload": {
                        "jsonrpc": "2.0", "id": mid, "result": {
                            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                            "serverInfo": {"name": "alphabot2-esp32", "version": "1.0.0"}}}}))
                elif method == "tools/list":
                    tool_list_sent = True
                    await ws.send(json.dumps({"session_id": session, "type": "mcp", "payload": {
                        "jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS_JSON}}}))
                elif method == "tools/call":
                    name = p["params"]["name"]
                    args = p["params"].get("arguments") or {}
                    if name in VERB:
                        verb, key, default = VERB[name]
                        value = args.get(key, default) if key else 0
                        commands.append(f"{verb}{value}" if key else verb)
                        text = f"running {verb}{value}" if key else "running S"
                    else:
                        text = '{"mode":"remote","moving":false,"queued":0,"viewers":0}'
                    await ws.send(json.dumps({"session_id": session, "type": "mcp", "payload": {
                        "jsonrpc": "2.0", "id": mid,
                        "result": {"content": [{"type": "text", "text": text}], "isError": False}}}))

        if pump_task:
            pump_task.cancel()

    print()
    print(f"  tools/list duoc hoi : {tool_list_sent}")
    print(f"  lenh xuong banh xe  : {commands if commands else 'KHONG CO'}")
    print(f"  cau tra loi         : {' '.join(replies)[:110]!r}")
    return 0 if commands else 1


sys.exit(asyncio.run(main()))
