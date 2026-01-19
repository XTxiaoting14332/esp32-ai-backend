import uasyncio as asyncio
import socket
import network
import urequests
import time
import gc
import json
import machine
import ntptime
from machine import I2S, Pin, PWM
from ws import AsyncWebsocketClient
from face import face_idle, blink_anim, face_sleepy, face_excited, face_thinking, face_disappointed, clear, display
from dns_server import DNSServer
import math
import machine
import array


CONFIG_FILE = "config.json"
boot_confirmed = False

class LogBuffer:
    def __init__(self, size=2048):
        self.buffer = []
        self.max_size = size
        self.current_size = 0
    def write(self, msg):
        line = f"[{time.time()}] {msg}"
        self.buffer.append(line)
        self.current_size += len(line)
        while self.current_size > self.max_size:
            self.current_size -= len(self.buffer.pop(0))
        print(line)

logger = LogBuffer()

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f: return json.load(f)
    except:
        return {
            "wifi_ssid": "", "wifi_pass": "", "server_ip": "192.168.1.100", 
            "server_port": 8032, "audio_mode": 1, "active_thershold": 250, 
            "silence_timeout": 1500, "thinking_timeout": 15000, 
            "sleep_time": 15, "chunk_size": 512, "volume": 0.5
        }

def save_config(c):
    with open(CONFIG_FILE, "w") as f: json.dump(c, f)

cfg = load_config()

is_recording, is_playing, is_thinking = False, False, False
thinking_start_time = 0
last_active_time = time.time()
curr_head_angle, curr_base_angle = 90, 90

servo_head = PWM(Pin(5), freq=50)
servo_base = PWM(Pin(4), freq=50)
mic, spk, udp_sock = None, None, None

def url_decode(s):
    s = s.replace('+', ' ')
    res = b""
    i = 0
    while i < len(s):
        if s[i] == '%':
            try:
                res += bytes([int(s[i+1:i+3], 16)])
                i += 3
            except: res += b'%'; i += 1
        else:
            res += s[i].encode('utf-8'); i += 1
    return res.decode('utf-8', 'ignore')

def release_servos():
    servo_head.duty(0); servo_base.duty(0)

async def smooth_set_servo(target_h, target_b, steps=20, delay=15):
    global curr_head_angle, curr_base_angle
    start_h, start_b = curr_head_angle, curr_base_angle
    for i in range(1, steps + 1):
        fraction = (math.sin((i / steps) * math.pi - math.pi / 2) + 1) / 2
        tmp_h = start_h + (target_h - start_h) * fraction
        tmp_b = start_b + (target_b - start_b) * fraction
        dh = int(((tmp_h / 180) * 2 + 0.52) / 20 * 1023)
        db = int(((tmp_b / 180) * 2 + 0.52) / 20 * 1023)
        servo_head.duty(dh); servo_base.duty(db)
        await asyncio.sleep_ms(delay)
    curr_head_angle, curr_base_angle = target_h, target_b

async def robot_action(action_type):
    global is_playing, last_active_time
    if not action_type: return
    

    machine.freq(80000000) 
    logger.write(f"Action: {action_type} (Freq down to 80MHz)")
    
    try:
        is_playing = True
        display.fill(0); face_excited(); display.show()
        await asyncio.sleep_ms(100)
        if action_type == "scared":
            await smooth_set_servo(110, 90, steps=10) 
            await smooth_set_servo(80, 90, steps=10)
        elif action_type == "nod":
            await smooth_set_servo(120, 90, steps=15)
            await smooth_set_servo(70, 90, steps=15)
        elif action_type == "shake":
            await smooth_set_servo(90, 60, steps=15)
            await smooth_set_servo(90, 120, steps=15)
            
        await smooth_set_servo(90, 90, steps=10)
        release_servos()
        
    finally:
        machine.freq(160000000)
        logger.write("Freq restored to 160MHz")
        
    is_playing = False
    last_active_time = time.time()


async def ui_task():
    global last_active_time
    sleep_frame = 0
    while True:
        if is_playing or is_recording:
            await asyncio.sleep_ms(100)
            continue 
            
        if is_thinking:
            face_thinking()
        else:
            if time.time() - last_active_time > cfg.get("sleep_time", 15):
                face_sleepy(sleep_frame % 4)
                sleep_frame += 1
            else:
                face_idle()
                await asyncio.sleep(1.5)
                if not (is_playing or is_recording or is_thinking):
                    blink_anim()
                    
        await asyncio.sleep_ms(200)

async def play_audio_task(url):
    global is_playing, is_thinking, last_active_time, spk
    machine.freq(240000000) 
    is_playing, is_thinking = True, False
    vol = cfg.get("volume", 0.5)
    
    try:
        spk = I2S(1, sck=Pin(15), ws=Pin(16), sd=Pin(7), 
                  mode=I2S.TX, bits=16, format=I2S.MONO, 
                  rate=16000, ibuf=12288)
        
        logger.write(f"Fetching TTS: {url}")
        res = urequests.get(url, stream=(cfg['audio_mode'] == 2))
        
        if res.status_code == 200:
            if cfg['audio_mode'] == 1:
                raw_data = res.content
                samples = array.array('h', raw_data)
                for i in range(len(samples)):
                    samples[i] = int(samples[i] * vol)
                spk.write(samples)
            else:
                while True:
                    chunk = res.raw.read(1024)
                    if not chunk or len(chunk) < 2: break
                    samples = array.array('h', chunk)
                    for i in range(len(samples)):
                        samples[i] = int(samples[i] * vol)
                    spk.write(samples)
                    await asyncio.sleep_ms(1)
        res.close()
        spk.write(bytearray(1024))
        await asyncio.sleep_ms(600)
    except Exception as e:
        logger.write(f"Audio Task Error: {e}")
    finally:
        if spk:
            spk.deinit()
            spk = None
        machine.freq(160000000)
        is_playing = False
        last_active_time = time.time()
        gc.collect()


async def mic_task(mic_obj, sock, addr):
    global is_recording, is_thinking, thinking_start_time, last_active_time
    c_size = cfg.get("chunk_size", 512)
    raw_buf, pcm16 = bytearray(c_size * 4), bytearray(c_size * 2)
    last_voice_at = 0
    while True:
        if (is_playing or is_thinking) and not is_recording:
            mic_obj.readinto(raw_buf); await asyncio.sleep_ms(20); continue
        n = mic_obj.readinto(raw_buf)
        if n > 0:
            curr_max = 0
            for i in range(c_size):
                b2, b3 = raw_buf[i*4 + 2], raw_buf[i*4 + 3]
                sample = (b3 << 8) | b2
                if sample > 32767: sample -= 65536
                if abs(sample) > curr_max: curr_max = abs(sample)
                pcm16[i*2], pcm16[i*2+1] = b2, b3
            if curr_max > cfg.get("active_thershold", 250):
                if not is_recording:
                    is_recording = True; logger.write("Voice Detected")
                    asyncio.create_task(robot_action("scared"))
                last_voice_at = time.ticks_ms(); last_active_time = time.time()
            if is_recording:
                sock.sendto(pcm16, addr)
                if time.ticks_diff(time.ticks_ms(), last_voice_at) > cfg.get("silence_timeout", 1500):
                    is_recording, is_thinking = False, True
                    thinking_start_time = time.ticks_ms(); logger.write("Thinking...")
        await asyncio.sleep_ms(5)


async def handle_http(reader, writer):
    global boot_confirmed, cfg
    try:
        raw_req = await reader.read(2048)
        request = raw_req.decode('utf-8', 'ignore')
        if "Host: 192.168.4.1" not in request and "GET / " in request:
            await writer.write("HTTP/1.1 302 Found\r\nLocation: http://192.168.4.1/\r\n\r\n")
            return

        header = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        
        if "GET /get_log" in request:
            await writer.write("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n" + "\n".join(logger.buffer))
        elif "GET /save" in request:
            try:
                start_index = request.find('?')
                end_index = request.find(' ', start_index)
                params_str = request[start_index + 1 : end_index]
                items = {p.split('=')[0]: url_decode(p.split('=')[1]) for p in params_str.split('&') if '=' in p}
                for key in ["port", "active_thershold", "silence_timeout", "thinking_timeout", "sleep_time", "chunk_size", "audio_mode"]:
                    if key in items: cfg[key] = int(items[key])
                cfg["wifi_ssid"], cfg["wifi_pass"] = items.get("ssid", ""), items.get("pass", "")
                cfg["server_ip"] = items.get("ip", "")
                cfg["volume"] = float(items["volume"])
                save_config(cfg)
                await writer.write(header + "Config Saved! <a href='/boot'>Boot Now</a>")
            except: pass
        elif "GET /boot" in request:
            boot_confirmed = True; await writer.write(header + "System Starting...")
        else:
            html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: sans-serif; background: #f0f2f5; padding: 15px; }}
        .card {{ background: #fff; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 15px; }}
        input, select {{ width: 100%; padding: 10px; margin: 5px 0; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }}
        .log {{ background: #222; color: #0f0; padding: 10px; height: 150px; overflow: auto; font-size: 11px; white-space: pre-wrap; }}
        .btn {{ background: #007bff; color: white; border: none; padding: 15px; width: 100%; border-radius: 5px; display: block; text-align: center; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="card">
        <h3>配置中心</h3>
        <form action="/save">
            WiFi 名称: <input name="ssid" value="{cfg['wifi_ssid']}">
            WiFi 密码: <input name="pass" type="password" value="{cfg['wifi_pass']}">
            服务器 IP: <input name="ip" value="{cfg['server_ip']}">
            服务器端口: <input name="port" value="{cfg['server_port']}">
            收音阈值: <input name="active_thershold" value="{cfg['active_thershold']}">
            静音超时(ms): <input name="silence_timeout" value="{cfg['silence_timeout']}">
            思考超时(ms): <input name="thinking_timeout" value="{cfg['thinking_timeout']}">
            音量(0.1-1.0): <input name="volume" value="{cfg['volume']}">
            采样块大小: <input name="chunk_size" value="{cfg['chunk_size']}">
            音频模式: 
            <select name="audio_mode">
                <option value="1" {"selected" if cfg['audio_mode']==1 else ""}>下载播放</option>
                <option value="2" {"selected" if cfg['audio_mode']==2 else ""}>流式播放</option>
            </select>
            <button type="submit" class="btn">保存所有配置</button>
        </form>
    </div>
    <div class="card">
        <h3>系统日志</h3>
        <div id="lb" class="log">正在载入日志...</div>
    </div>
    <a href="/boot" class="btn" style="background:#28a745;">启动机器人</a>
    <script>
        setInterval(() => {{
            fetch('/get_log').then(r => r.text()).then(t => {{
                let b = document.getElementById('lb'); b.innerText = t; b.scrollTop = b.scrollHeight;
            }});
        }}, 2000);
    </script>
</body>
</html>"""
            await writer.write(header + html)
    except: pass
    finally: await writer.drain(); writer.close()


async def main():
    await asyncio.sleep_ms(1000)
    machine.freq(80000000)
    await smooth_set_servo(90, curr_base_angle, steps=30)
    await asyncio.sleep_ms(500)
    await smooth_set_servo(curr_head_angle, 90, steps=30)
    release_servos()
    machine.freq(160000000)
    global boot_confirmed, cfg, mic, is_thinking, is_recording, last_active_time
    logger.write("Power on: Resetting Servos...")
    ap = network.WLAN(network.AP_IF); ap.active(True)
    ap.ifconfig(('192.168.4.1', '255.255.255.0', '192.168.4.1', '192.168.4.1'))
    ap.config(essid='Robot-Setup')
    dns = DNSServer(ip="192.168.4.1"); asyncio.create_task(dns.run())
    http = await asyncio.start_server(handle_http, "0.0.0.0", 80)
    for i in range(60, 0, -1):
        if boot_confirmed: break
        display.fill(0); display.text("CONFIG MODE", 25, 10, 1)
        display.text("192.168.4.1", 25, 30, 1)
        display.text(f"Wait: {i}s", 40, 50, 1); display.show()
        await asyncio.sleep(1)

    http.close(); ap.active(False); gc.collect()
    wlan = network.WLAN(network.STA_IF); wlan.active(True)
    wlan.connect(cfg["wifi_ssid"], cfg["wifi_pass"])
    retry = 0
    while not wlan.isconnected() and retry < 20:
        display.fill(0); display.text(f"WiFi Connecting:{retry}", 10, 30, 1); display.show()
        await asyncio.sleep(1); retry += 1
    
    if not wlan.isconnected(): display.fill(0); display.text(f"Wifi Error", 10, 30, 1); display.show()

    curr_ip = wlan.ifconfig()[0]
    display.fill(0); display.text("Connected!", 35, 10, 1); display.text(f"IP:{curr_ip}", 0, 35, 1); display.show(); await asyncio.sleep(3)
    wlan.config(pm=wlan.PM_POWERSAVE)

    try:
        ws_url = f"ws://{cfg['server_ip']}:{cfg['server_port']}/esp32/backend/ws"
        tts_url = f"http://{cfg['server_ip']}:{cfg['server_port']}/esp32/backend/tts"
        mic = I2S(0, sck=Pin(12), ws=Pin(11), sd=Pin(10), mode=I2S.RX, bits=32, format=I2S.MONO, rate=16000, ibuf=12288)
        udp_addr = (cfg['server_ip'], cfg['server_port'])
        ws = AsyncWebsocketClient(); await ws.handshake(ws_url)
        
        asyncio.create_task(ui_task())
        asyncio.create_task(mic_task(mic, socket.socket(socket.AF_INET, socket.SOCK_DGRAM), udp_addr))
        
        while True:
            if is_thinking and (time.ticks_ms() - thinking_start_time > cfg['thinking_timeout']):
                face_disappointed(); is_thinking = False; last_active_time = time.time()
                
            msg = await ws.recv()
            if msg:
                data = json.loads(msg)
                if data.get("action") == "play_audio":
                    is_thinking = False
                    is_playing = True
                    await robot_action(data.get("data")) 
                    await play_audio_task(tts_url)
                    is_playing = False
                    await ws.send(json.dumps({"action": "udp_unlock"}))
                elif data.get("action") == "no_data":
                    logger.write("No Data from Server")
                    face_disappointed(); is_thinking = False; is_recording = False
                    if mic: 
                        tmp = bytearray(1024); mic.readinto(tmp)
                    await ws.send(json.dumps({"action": "udp_unlock"}))
                    await asyncio.sleep(1.5); last_active_time = time.time()
            await asyncio.sleep_ms(10)
    except: display.fill(0); display.text(f"WS Error", 10, 30, 1); display.show()

if __name__ == "__main__": asyncio.run(main())