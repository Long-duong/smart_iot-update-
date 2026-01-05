import cv2
import numpy as np
import time
import os
import json
import threading
import requests
from datetime import datetime
import secrets

# ================= CẤU HÌNH SERVER & ESP =================
SERVER_URL = "http://localhost:3000"  
DATASET_DIR = "faces_db"
YUNET_MODEL = "face_detection_yunet_2023mar.onnx"

# Ngưỡng cảnh báo
ABSENT_THRESHOLD = 1
TEMP_THRESHOLD = 30

# Cấu hình ESP8266
ESP_IP = "192.168.1.100"
ESP_USER = "admin"
ESP_PASS = "1234"

# ================= CLASS ĐIỀU KHIỂN ESP8266 =================
class ESP8266Controller:
    def __init__(self):
        self.auth = (ESP_USER, ESP_PASS)
        self.connection_status = False
        self.lock = threading.Lock()

    def led(self, red=False, yellow=False):
        def _send():
            with self.lock:
                try:
                    requests.post(f"http://{ESP_IP}/led", json={"red": red, "yellow": yellow}, auth=self.auth, timeout=1)
                    self.connection_status = True
                except:
                    self.connection_status = False
        threading.Thread(target=_send).start()

    def get_temp_humidity(self):
        try:
            r = requests.get(f"http://{ESP_IP}/dht11", auth=self.auth, timeout=1)
            if r.status_code == 200:
                j = r.json()
                return j.get("temp"), j.get("humidity")
        except:
            pass
        return None, None

# ================= CLASS XỬ LÝ AI =================
class SmartMonitor:
    def __init__(self):
        print("▶ SMART CLASSROOM – AI CLIENT (ADVANCED LOGIC)")
        print(f"📡 Server: {SERVER_URL}")

        self.download_model_if_needed()

        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        self.detector = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (320, 320), 0.7, 0.3)
        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        
        self.labels = {}
        self.uniforms = {}
        self.load_faces()

        self.esp = ESP8266Controller()
        self.running = True

        # === DANH SÁCH CHẶN SPAM ===
        self.logged_attendance = set()
        self.logged_uniform = set()
        self.violation_cooldown = {}

    def download_model_if_needed(self):
        if not os.path.exists(YUNET_MODEL):
            print("⬇️ Đang tải model...")
            url = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
            with open(YUNET_MODEL, "wb") as f:
                f.write(requests.get(url).content)

    def load_faces(self):
        faces, ids = [], []
        idx = 0
        
        if os.path.exists(os.path.join(DATASET_DIR, "metadata.json")):
            try:
                with open(os.path.join(DATASET_DIR, "metadata.json"), "r") as f:
                    self.uniforms = json.load(f).get("uniforms", {})
            except: pass

        if not os.path.exists(DATASET_DIR): os.makedirs(DATASET_DIR)

        for name in os.listdir(DATASET_DIR):
            p = os.path.join(DATASET_DIR, name)
            if not os.path.isdir(p): continue
            
            self.labels[idx] = name
            has_img = False
            for img_path in os.listdir(p):
                if img_path.lower().endswith(('jpg','png','jpeg')):
                    g = cv2.imread(os.path.join(p, img_path), cv2.IMREAD_GRAYSCALE)
                    if g is not None:
                        faces.append(cv2.resize(g, (200, 200)))
                        ids.append(idx)
                        has_img = True
            if has_img: idx += 1

        if faces:
            self.recognizer.train(faces, np.array(ids))
            print(f"✅ Đã load {len(self.labels)} sinh viên.")
        else:
            print("⚠️ Chưa có dữ liệu khuôn mặt!")

    # --- HÀM GỬI API ---
    def api_send_attendance(self, name):
        if name in self.logged_attendance: return
        self.logged_attendance.add(name)

        def _req():
            try:
                print(f"⬆️ Đang gửi điểm danh: {name}")
                requests.post(f"{SERVER_URL}/api/attendance", json={"name": name}, timeout=2)
                print(f"✅ Điểm danh thành công: {name}")
            except: pass
        threading.Thread(target=_req).start()

    def api_send_violation(self, name, v_type):
        now = time.time()
        if "dong phuc" in v_type or "Đồng phục" in v_type:
            if name in self.logged_uniform: return
            self.logged_uniform.add(name)
        else:
            key = f"{name}_{v_type}"
            if key in self.violation_cooldown and (now - self.violation_cooldown[key] < 30):
                return
            self.violation_cooldown[key] = now

        def _req():
            try:
                print(f"⬆️ Đang gửi cảnh báo: {name} - {v_type}")
                requests.post(f"{SERVER_URL}/api/report", json={"name": name, "type": v_type}, timeout=2)
            except: pass
        threading.Thread(target=_req).start()

    def api_send_env(self, temp, hum):
        def _req():
            try:
                requests.post(f"{SERVER_URL}/api/env", json={"temp": temp, "hum": hum}, timeout=2)
            except: pass
        threading.Thread(target=_req).start()

    # --- LOGIC NHẬN DIỆN & KIỂM TRA ---
    def recognize(self, gray, box):
        x, y, w, h = box
        roi = gray[y:y+h, x:x+w]
        if roi.size == 0: return "Unknown"
        try:
            roi = cv2.resize(roi, (200, 200))
            label, conf = self.recognizer.predict(roi)
            if conf < 85: return self.labels.get(label, "Unknown")
        except: pass
        return "Unknown"

    def check_uniform(self, frame, box):
        x, y, w, h = box
        roi_y = min(y + h, frame.shape[0])
        roi_y_end = min(y + h + 80, frame.shape[0])
        if roi_y >= roi_y_end: return "unknown"
        
        roi = frame[roi_y:roi_y_end, max(0, x):min(x+w, frame.shape[1])]
        if roi.size == 0: return "unknown"

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        lower = np.array([0, 0, 168])
        upper = np.array([172, 111, 255])
        mask = cv2.inRange(hsv, lower, upper)
        return "white" if cv2.countNonZero(mask) / mask.size > 0.3 else "other"

    def check_turning_head_landmarks(self, face_data):
        """
        Sử dụng Landmarks để phát hiện quay đầu chính xác.
        YuNet landmarks:
        - 4,5: Mắt phải (Right Eye)
        - 6,7: Mắt trái (Left Eye)
        - 8,9: Mũi (Nose Tip)
        """
        x_re = face_data[4] # Right eye X
        x_le = face_data[6] # Left eye X
        x_nose = face_data[8] # Nose X

        # Khoảng cách giữa 2 mắt
        eye_distance = abs(x_le - x_re)
        if eye_distance == 0: return False

        # Trung điểm của 2 mắt
        x_mid = (x_re + x_le) / 2

        # Độ lệch của mũi so với trung điểm
        nose_offset = abs(x_nose - x_mid)

        # Nếu mũi lệch quá 40% so với khoảng cách 2 mắt -> Quay đầu
        # (Bình thường mũi ở giữa, offset gần 0)
        ratio = nose_offset / eye_distance
        
        return ratio > 0.45  # Ngưỡng (0.4 - 0.5 là hợp lý)

    def run(self):
        last_env_time = 0
        print("📷 Camera đang chạy... Nhấn 'q' để thoát.")
        
        while self.running:
            ret, frame = self.cap.read()
            if not ret: break

            h, w = frame.shape[:2]
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(frame)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if faces is not None:
                for f in faces:
                    # f[:4] là bounding box, f[4:14] là 5 landmarks
                    box = list(map(int, f[:4]))
                    
                    # === 1. NHẬN DIỆN ===
                    name = self.recognize(gray, box)
                    
                    if name != "Unknown":
                        self.api_send_attendance(name)

                        violation = ""
                        
                        # === 2. CHECK LỖI (LOGIC MỚI) ===
                        
                        # A. Quay đầu (Dùng Landmarks - Chính xác hơn)
                        if self.check_turning_head_landmarks(f):
                            violation = "Gian lan (Quay dau)"
                            self.esp.led(red=True)
                        
                        # B. Ngủ gật (Đầu cúi thấp)
                        elif box[1] > h * 0.6:
                            violation = "Ngu gat"
                            self.esp.led(yellow=True)
                        
                        # C. Đồng phục
                        else:
                            u_color = self.check_uniform(frame, box)
                            if u_color != "unknown" and u_color != "white":
                                violation = "Sai dong phuc"
                        
                        if violation:
                            self.api_send_violation(name, violation)
                        else:
                            if int(time.time()) % 5 == 0: 
                                self.esp.led(red=False, yellow=False)

                    # Vẽ hình
                    color = (0, 0, 255) if violation else (0, 255, 0)
                    cv2.rectangle(frame, (box[0], box[1]), (box[0]+box[2], box[1]+box[3]), color, 2)
                    
                    # Vẽ landmarks (Mắt, Mũi) để debug
                    cv2.circle(frame, (int(f[4]), int(f[5])), 2, (255, 0, 0), -1) # Mắt phải
                    cv2.circle(frame, (int(f[6]), int(f[7])), 2, (0, 0, 255), -1) # Mắt trái
                    cv2.circle(frame, (int(f[8]), int(f[9])), 2, (0, 255, 255), -1) # Mũi

                    label = f"{name}"
                    if violation: label += f" - {violation}"
                    cv2.putText(frame, label, (box[0], box[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # Gửi môi trường
            if time.time() - last_env_time > 10:
                t, hmd = self.esp.get_temp_humidity()
                if t: self.api_send_env(t, hmd)
                last_env_time = time.time()

            cv2.imshow("AI Client", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"): break

        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    try:
        SmartMonitor().run()
    except Exception as e:
        print(f"❌ Error: {e}")
