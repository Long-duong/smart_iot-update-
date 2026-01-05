import cv2
import numpy as np
import time
import os
import json
import threading
import requests
from datetime import datetime
from flask import Flask, jsonify, request, render_template_string, session
from flask_socketio import SocketIO, emit, disconnect
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

# ================= CONFIG =================
DATASET_DIR = "faces_db"
YUNET_MODEL = "face_detection_yunet_2023mar.onnx"

ABSENT_THRESHOLD = 1
TEMP_THRESHOLD = 30

ESP_IP = "192.168.1.100"
ESP_USER = "admin"
ESP_PASS = "1234"

# Cấu hình bảo mật
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("admin123")
SESSION_TIMEOUT = 3600

# =========================================

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['PERMANENT_SESSION_LIFETIME'] = SESSION_TIMEOUT
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)
monitor = None

# ============ DATABASE ====================
users_db = {
    ADMIN_USERNAME: {
        "password_hash": ADMIN_PASSWORD_HASH,
        "role": "admin",
        "esp_control": True
    }
}

active_sessions = {}

def generate_esp_token():
    return secrets.token_urlsafe(32)

def verify_session(sid):
    if sid not in active_sessions:
        return False
    
    session_data = active_sessions[sid]
    if time.time() - session_data['last_activity'] > SESSION_TIMEOUT:
        del active_sessions[sid]
        return False
    
    session_data['last_activity'] = time.time()
    return True

def verify_esp_control(sid):
    if not verify_session(sid):
        return False
    
    username = active_sessions[sid]['username']
    return users_db.get(username, {}).get('esp_control', False)

# ============ ESP =========================
class ESP8266Controller:
    def __init__(self):
        self.auth = (ESP_USER, ESP_PASS)
        self.last_led_state = {"red": False, "yellow": False}
        self.connection_status = False
        self.lock = threading.Lock()

    def led(self, red=False, yellow=False, token=None):
        if (red != self.last_led_state["red"] or yellow != self.last_led_state["yellow"]):
            if token != "auto" and not self._verify_token(token):
                print("⚠ Lệnh LED bị từ chối: Token không hợp lệ")
                return False

        with self.lock:
            try:
                r = requests.post(
                    f"http://{ESP_IP}/led",
                    json={"red": red, "yellow": yellow},
                    auth=self.auth,
                    timeout=2
                )
                if r.status_code == 200:
                    self.last_led_state = {"red": red, "yellow": yellow}
                    self.connection_status = True
                    return True
            except Exception as e:
                self.connection_status = False
        return False

    def temp_humidity(self):
        try:
            r = requests.get(f"http://{ESP_IP}/dht11", auth=self.auth, timeout=2)
            if r.status_code == 200:
                j = r.json()
                self.connection_status = True
                return j.get("temp"), j.get("humidity")
        except:
            self.connection_status = False
        return None, None

    def _verify_token(self, token):
        if not token:
            return False
        for session_data in active_sessions.values():
            if session_data.get('esp_token') == token:
                return True
        return False

    def get_status(self):
        return {
            "connected": self.connection_status,
            "led_state": self.last_led_state
        }

# ============ SMART CLASS =================
class SmartMonitor:
    def __init__(self):
        print("▶ SMART CLASSROOM – ENHANCED VERSION")

        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            print("⚠ Không thể mở camera!")
            return

        cv2.namedWindow("Smart Classroom", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Smart Classroom", 1280, 720)

        self.detector = cv2.FaceDetectorYN.create(
            YUNET_MODEL, "", (320, 320),
            score_threshold=0.7,
            nms_threshold=0.3
        )

        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.labels = {}
        self.uniforms = {}

        # Khởi tạo stats TRƯỚC khi load_faces
        self.stats = {
            "present": [],
            "absent": [],
            "violations": {},
            "temp": None,
            "humidity": None,
            "time": "",
            "fps": 0,
            "esp_status": "disconnected",
            "total_students": 0
        }

        self.esp = ESP8266Controller()
        self.violations = {}
        self.absent_warned = False
        self.frame_count = 0
        self.fps = 0
        self.last_fps_time = time.time()
        self.running = True

        # Load faces SAU khi khởi tạo stats
        self.load_faces()

    def load_faces(self):
        faces, ids = [], []
        idx = 0

        meta = os.path.join(DATASET_DIR, "metadata.json")
        if os.path.exists(meta):
            with open(meta, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.uniforms = data.get("uniforms", {})

        for name in os.listdir(DATASET_DIR):
            p = os.path.join(DATASET_DIR, name)
            if not os.path.isdir(p):
                continue

            self.labels[idx] = name
            count = 0
            for img in os.listdir(p):
                g = cv2.imread(os.path.join(p, img), cv2.IMREAD_GRAYSCALE)
                if g is not None:
                    faces.append(g)
                    ids.append(idx)
                    count += 1
            
            if count > 0:
                print(f"  - {name}: {count} ảnh")
            idx += 1

        if faces:
            self.recognizer.train(faces, np.array(ids))
            print(f"✓ Đã load {len(self.labels)} sinh viên")
            self.stats["total_students"] = len(self.labels)
        else:
            print("⚠ Không có dữ liệu khuôn mặt!")

    def recognize(self, gray, box):
        x, y, w, h = box
        if y < 0 or x < 0 or y+h > gray.shape[0] or x+w > gray.shape[1]:
            return "Unknown"
        
        roi = gray[y:y+h, x:x+w]
        if roi.size == 0:
            return "Unknown"
        
        try:
            label, conf = self.recognizer.predict(roi)
            if conf < 85:
                return self.labels.get(label, "Unknown")
        except:
            pass
        return "Unknown"

    def check_uniform(self, frame, box):
        x, y, w, h = box
        roi_y = min(y+h, frame.shape[0])
        roi_y_end = min(y+h+60, frame.shape[0])
        
        if roi_y >= roi_y_end:
            return "unknown"
        
        roi = frame[roi_y:roi_y_end, max(0, x):min(x+w, frame.shape[1])]
        if roi.size == 0:
            return "unknown"

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        white = cv2.inRange(hsv, (0, 0, 200), (180, 40, 255))

        return "white" if cv2.countNonZero(white) / white.size > 0.3 else "other"

    def turning_head(self, w, h):
        r = w / h if h > 0 else 1
        return r < 0.75 or r > 1.3

    def sleeping(self, y, frame_h):
        return y > frame_h * 0.6

    def report(self, name, msg):
        if name == "Unknown":
            return

        if name not in self.violations:
            self.violations[name] = []

        if msg not in self.violations[name]:
            t = datetime.now().strftime("%H:%M:%S")
            self.violations[name].append(msg)
            print(f"⚠ [{t}] {name} - {msg}")

            socketio.emit("violation", {
                "name": name,
                "type": msg,
                "time": t
            })

            if "GIAN LẬN" in msg:
                self.esp.led(red=True, yellow=False, token="auto")
                threading.Timer(3, lambda: self.esp.led(red=False, yellow=False, token="auto")).start()

    def run(self):
        last_temp = 0
        skip_frames = 2

        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            self.frame_count += 1
            
            if time.time() - self.last_fps_time >= 1.0:
                self.fps = self.frame_count
                self.frame_count = 0
                self.last_fps_time = time.time()

            if self.frame_count % skip_frames != 0:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            h, w = frame.shape[:2]

            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(frame)

            present = []

            if faces is not None:
                for f in faces:
                    x, y, bw, bh = map(int, f[:4])
                    name = self.recognize(gray, (x, y, bw, bh))

                    if name != "Unknown" and name not in present:
                        present.append(name)

                        if self.turning_head(bw, bh):
                            self.report(name, "GIAN LẬN (Quay đầu)")

                        if self.sleeping(y, h):
                            self.report(name, "NGỦ GẬT")

                        uniform = self.check_uniform(frame, (x, y, bw, bh))
                        expected = self.uniforms.get(name, "white")
                        if uniform != "unknown" and uniform != expected:
                            self.report(name, "SAI ĐỒNG PHỤC")

                    color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
                    cv2.rectangle(frame, (x, y), (x+bw, y+bh), color, 2)
                    cv2.putText(frame, name, (x, y-10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            absent = list(set(self.labels.values()) - set(present))
            if len(absent) >= ABSENT_THRESHOLD and not self.absent_warned:
                print(f"⚠ VẮNG MẶT ({len(absent)}): {', '.join(absent)}")
                self.absent_warned = True

            if time.time() - last_temp > 5:
                t, hmd = self.esp.temp_humidity()
                self.stats["temp"] = t
                self.stats["humidity"] = hmd
                
                if t and t > TEMP_THRESHOLD:
                    self.esp.led(red=False, yellow=True, token="auto")
                elif t:
                    self.esp.led(red=False, yellow=False, token="auto")
                    
                last_temp = time.time()

            self.stats.update({
                "present": present,
                "absent": absent,
                "violations": self.violations,
                "time": datetime.now().isoformat(),
                "fps": self.fps,
                "esp_status": "connected" if self.esp.connection_status else "disconnected"
            })

            cv2.putText(frame, f"FPS: {self.fps}", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f"Present: {len(present)}/{len(self.labels)}", (10, 60),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            cv2.imshow("Smart Classroom", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        self.cap.release()
        cv2.destroyAllWindows()

    def stop(self):
        self.running = False

# ============ WEB DASHBOARD ===============
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Classroom Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        .header {
            background: white;
            border-radius: 15px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .header h1 {
            color: #667eea;
            margin-bottom: 10px;
        }
        .login-box {
            background: white;
            border-radius: 15px;
            padding: 40px;
            max-width: 400px;
            margin: 100px auto;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .login-box h2 {
            color: #667eea;
            margin-bottom: 30px;
            text-align: center;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 500;
        }
        .form-group input {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.3s;
        }
        .form-group input:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .btn:hover {
            transform: translateY(-2px);
        }
        .btn-danger {
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .card h3 {
            color: #667eea;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .stat-number {
            font-size: 48px;
            font-weight: bold;
            color: #667eea;
            margin: 10px 0;
        }
        .status {
            display: inline-block;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 600;
        }
        .status.online {
            background: #d4edda;
            color: #155724;
        }
        .status.offline {
            background: #f8d7da;
            color: #721c24;
        }
        .led-control {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }
        .led-btn {
            flex: 1;
            padding: 12px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .led-btn:hover {
            transform: scale(1.05);
        }
        .led-btn.red {
            background: #ff4757;
            color: white;
        }
        .led-btn.yellow {
            background: #ffa502;
            color: white;
        }
        .led-btn.off {
            background: #2f3542;
            color: white;
        }
        .violation-list {
            max-height: 300px;
            overflow-y: auto;
        }
        .violation-item {
            background: #fff3cd;
            border-left: 4px solid #ffa502;
            padding: 12px;
            margin-bottom: 10px;
            border-radius: 5px;
        }
        .violation-item.critical {
            background: #f8d7da;
            border-left-color: #ff4757;
        }
        .student-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        .student-tag {
            padding: 8px 16px;
            background: #e7f3ff;
            border-radius: 20px;
            color: #0066cc;
            font-size: 14px;
            font-weight: 500;
        }
        .student-tag.absent {
            background: #ffe7e7;
            color: #cc0000;
        }
        .error {
            color: #ff4757;
            text-align: center;
            margin-top: 10px;
        }
        .info-row {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #e0e0e0;
        }
        .info-row:last-child {
            border-bottom: none;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .live-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            background: #00ff00;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
    </style>
</head>
<body>
    <div class="container">
        <div id="loginPage" class="login-box">
            <h2>🔐 Đăng nhập</h2>
            <form id="loginForm">
                <div class="form-group">
                    <label>Tên đăng nhập</label>
                    <input type="text" id="username" required>
                </div>
                <div class="form-group">
                    <label>Mật khẩu</label>
                    <input type="password" id="password" required>
                </div>
                <button type="submit" class="btn">Đăng nhập</button>
                <div id="loginError" class="error"></div>
            </form>
        </div>

        <div id="dashboardPage" style="display: none;">
            <div class="header">
                <h1>🎓 Smart Classroom Dashboard</h1>
                <p>Giám sát lớp học thông minh - <span class="live-indicator"></span> LIVE</p>
                <button class="btn btn-danger" onclick="logout()" style="max-width: 200px; margin-top: 15px;">Đăng xuất</button>
            </div>

            <div class="grid">
                <div class="card">
                    <h3>📊 Thống kê</h3>
                    <div class="info-row">
                        <span>Tổng sinh viên:</span>
                        <strong id="totalStudents">0</strong>
                    </div>
                    <div class="info-row">
                        <span>Có mặt:</span>
                        <strong id="presentCount" style="color: #00c853;">0</strong>
                    </div>
                    <div class="info-row">
                        <span>Vắng mặt:</span>
                        <strong id="absentCount" style="color: #ff4757;">0</strong>
                    </div>
                    <div class="info-row">
                        <span>FPS:</span>
                        <strong id="fps">0</strong>
                    </div>
                </div>

                <div class="card">
                    <h3>🌡️ Môi trường</h3>
                    <div class="info-row">
                        <span>Nhiệt độ:</span>
                        <strong id="temperature">--</strong>
                    </div>
                    <div class="info-row">
                        <span>Độ ẩm:</span>
                        <strong id="humidity">--</strong>
                    </div>
                    <div class="info-row">
                        <span>ESP8266:</span>
                        <span id="espStatus" class="status offline">Offline</span>
                    </div>
                </div>

                <div class="card">
                    <h3>💡 Điều khiển LED</h3>
                    <div class="led-control">
                        <button class="led-btn red" onclick="controlLED('red')">🔴 Đỏ</button>
                        <button class="led-btn yellow" onclick="controlLED('yellow')">🟡 Vàng</button>
                        <button class="led-btn off" onclick="controlLED('off')">⚫ Tắt</button>
                    </div>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h3>✅ Có mặt (<span id="presentCount2">0</span>)</h3>
                    <div class="student-list" id="presentList"></div>
                </div>

                <div class="card">
                    <h3>❌ Vắng mặt (<span id="absentCount2">0</span>)</h3>
                    <div class="student-list" id="absentList"></div>
                </div>
            </div>

            <div class="card">
                <h3>⚠️ Vi phạm</h3>
                <div class="violation-list" id="violationList">
                    <p style="text-align: center; color: #999;">Chưa có vi phạm nào</p>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <script>
        let sessionId = null;
        let espToken = null;
        let socket = null;

        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;

            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });

                const data = await res.json();
                if (data.success) {
                    sessionId = data.session_id;
                    espToken = data.esp_token;
                    document.getElementById('loginPage').style.display = 'none';
                    document.getElementById('dashboardPage').style.display = 'block';
                    startDashboard();
                } else {
                    document.getElementById('loginError').textContent = data.message;
                }
            } catch (err) {
                document.getElementById('loginError').textContent = 'Lỗi kết nối server';
            }
        });

        function logout() {
            fetch('/api/logout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId })
            });
            location.reload();
        }

        async function controlLED(type) {
            if (!espToken) {
                alert('Bạn không có quyền điều khiển ESP');
                return;
            }

            const states = {
                'red': { red: true, yellow: false },
                'yellow': { red: false, yellow: true },
                'off': { red: false, yellow: false }
            };

            try {
                const res = await fetch('/api/esp/led', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Session-ID': sessionId
                    },
                    body: JSON.stringify(states[type])
                });
                const data = await res.json();
                if (!data.success) {
                    alert('Không thể điều khiển LED');
                }
            } catch (err) {
                alert('Lỗi kết nối ESP');
            }
        }

        function startDashboard() {
            socket = io({ query: { session_id: sessionId } });

            socket.on('violation', (data) => {
                addViolation(data);
            });

            setInterval(updateStats, 1000);
        }

        async function updateStats() {
            try {
                const res = await fetch('/api/stats', {
                    headers: { 'X-Session-ID': sessionId }
                });
                const data = await res.json();

                document.getElementById('totalStudents').textContent = data.total_students || 0;
                document.getElementById('presentCount').textContent = data.present?.length || 0;
                document.getElementById('presentCount2').textContent = data.present?.length || 0;
                document.getElementById('absentCount').textContent = data.absent?.length || 0;
                document.getElementById('absentCount2').textContent = data.absent?.length || 0;
                document.getElementById('fps').textContent = data.fps || 0;

                document.getElementById('temperature').textContent = data.temp ? data.temp + '°C' : '--';
                document.getElementById('humidity').textContent = data.humidity ? data.humidity + '%' : '--';

                const espStatus = document.getElementById('espStatus');
                if (data.esp_status === 'connected') {
                    espStatus.textContent = 'Online';
                    espStatus.className = 'status online';
                } else {
                    espStatus.textContent = 'Offline';
                    espStatus.className = 'status offline';
                }

                updateStudentLists(data.present, data.absent);
            } catch (err) {
                console.error('Error updating stats:', err);
            }
        }

        function updateStudentLists(present, absent) {
            const presentList = document.getElementById('presentList');
            const absentList = document.getElementById('absentList');

            presentList.innerHTML = present?.map(name => 
                `<div class="student-tag">${name}</div>`
            ).join('') || '<p style="color: #999;">Không có</p>';

            absentList.innerHTML = absent?.map(name => 
                `<div class="student-tag absent">${name}</div>`
            ).join('') || '<p style="color: #999;">Không có</p>';
        }

        function addViolation(data) {
            const list = document.getElementById('violationList');
            if (list.querySelector('p')) {
                list.innerHTML = '';
            }

            const item = document.createElement('div');
            item.className = 'violation-item' + (data.type.includes('GIAN LẬN') ? ' critical' : '');
            item.innerHTML = `
                <strong>${data.name}</strong> - ${data.type}<br>
                <small>${data.time}</small>
            `;
            list.insertBefore(item, list.firstChild);
        }
    </script>
</body>
</html>
"""

# ============ WEB ROUTES ==================
@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"success": False, "message": "Thiếu thông tin"}), 400

    user = users_db.get(username)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"success": False, "message": "Sai tên đăng nhập hoặc mật khẩu"}), 401

    session_id = secrets.token_urlsafe(32)
    esp_token = generate_esp_token() if user.get("esp_control") else None
    
    active_sessions[session_id] = {
        "username": username,
        "last_activity": time.time(),
        "esp_token": esp_token,
        "role": user.get("role")
    }

    return jsonify({
        "success": True,
        "session_id": session_id,
        "esp_token": esp_token,
        "username": username,
        "role": user.get("role"),
        "esp_control": user.get("esp_control", False)
    })

@app.route("/api/logout", methods=["POST"])
def logout():
    data = request.get_json()
    session_id = data.get("session_id")
    
    if session_id in active_sessions:
        del active_sessions[session_id]
    
    return jsonify({"success": True})

@app.route("/api/stats")
def api_stats():
    session_id = request.headers.get("X-Session-ID")
    
    if not verify_session(session_id):
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify(monitor.stats if monitor else {})

@app.route("/api/violations")
def api_violations():
    session_id = request.headers.get("X-Session-ID")
    
    if not verify_session(session_id):
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify(monitor.violations if monitor else {})

@app.route("/api/esp/led", methods=["POST"])
def api_esp_led():
    session_id = request.headers.get("X-Session-ID")
    
    if not verify_esp_control(session_id):
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.get_json()
    red = data.get("red", False)
    yellow = data.get("yellow", False)
    
    token = active_sessions[session_id].get("esp_token")
    success = monitor.esp.led(red=red, yellow=yellow, token=token)
    
    return jsonify({
        "success": success,
        "state": monitor.esp.last_led_state
    })

@app.route("/api/esp/status")
def api_esp_status():
    session_id = request.headers.get("X-Session-ID")
    
    if not verify_session(session_id):
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify(monitor.esp.get_status() if monitor else {})

@socketio.on("connect")
def handle_connect():
    session_id = request.args.get("session_id")
    
    if not verify_session(session_id):
        disconnect()
        return False
    
    print(f"✓ Client kết nối: {session_id[:8]}...")
    emit("connected", {"message": "Connected to Smart Classroom"})

@socketio.on("disconnect")
def handle_disconnect():
    print("⚠ Client ngắt kết nối")

def start_monitor():
    global monitor
    try:
        monitor = SmartMonitor()
        print("✅ Camera và Monitor khởi động thành công")
        monitor.run()
    except Exception as e:
        print(f"❌ Lỗi khởi động monitor: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # Tắt log HTTP để giảm spam
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    threading.Thread(target=start_monitor, daemon=True).start()
    
    print("=" * 60)
    print("🚀 SMART CLASSROOM SYSTEM - READY")
    print("=" * 60)
    print(f"📡 Dashboard: http://0.0.0.0:5000")
    print(f"🔐 Login: {ADMIN_USERNAME} / admin123")
    print(f"⚠️  ĐỔI MẬT KHẨU MẶC ĐỊNH NGAY!")
    print(f"🎥 Camera window: 'Smart Classroom'")
    print(f"🛑 Nhấn 'q' trong cửa sổ camera để thoát")
    print("=" * 60)
    
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, log_output=False)
