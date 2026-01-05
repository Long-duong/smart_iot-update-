import cv2
import numpy as np
import requests
import os
import time
import threading
import json

# ================= CẤU HÌNH SERVER =================
# Chỉ cần địa chỉ Server Node.js
SERVER_URL = "http://localhost:3000" 

# Cấu hình Model AI
DATASET_DIR = "faces_db"
YUNET_MODEL = "face_detection_yunet_2023mar.onnx"

# ================= CLASS XỬ LÝ AI =================
class SmartMonitor:
    def __init__(self):
        print("▶ SMART CLASSROOM - AI CLIENT (SERVER MODE)")
        print(f"📡 Kết nối tới: {SERVER_URL}")

        self.download_model()
        
        # Cấu hình Camera
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        # Khởi tạo AI
        self.detector = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (320, 320), 0.7, 0.3)
        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        
        self.labels = {}
        self.uniforms = {}
        self.load_data()
        
        self.running = True
        
        # Biến chống spam (Cache)
        self.logged_attendance = set()
        self.logged_uniform = set()
        self.violation_cooldown = {}

    def download_model(self):
        if not os.path.exists(YUNET_MODEL):
            print("⬇️ Đang tải model AI...")
            url = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
            with open(YUNET_MODEL, "wb") as f:
                f.write(requests.get(url).content)

    def load_data(self):
        if not os.path.exists(DATASET_DIR): os.makedirs(DATASET_DIR)
        
        # Load đồng phục
        try:
            with open(os.path.join(DATASET_DIR, "metadata.json"), "r") as f:
                self.uniforms = json.load(f).get("uniforms", {})
        except: pass

        # Load khuôn mặt
        faces, ids = [], []
        idx = 0
        for name in os.listdir(DATASET_DIR):
            p = os.path.join(DATASET_DIR, name)
            if not os.path.isdir(p): continue
            
            self.labels[idx] = name
            has_img = False
            for img in os.listdir(p):
                if img.endswith(('jpg','png','jpeg')):
                    g = cv2.imread(os.path.join(p, img), cv2.IMREAD_GRAYSCALE)
                    if g is not None:
                        faces.append(cv2.resize(g, (200, 200)))
                        ids.append(idx)
                        has_img = True
            if has_img: idx += 1

        if faces:
            self.recognizer.train(faces, np.array(ids))
            print(f"✅ Đã học dữ liệu của {len(self.labels)} sinh viên.")

    # --- GỬI DỮ LIỆU LÊN SERVER ---
    def send_api(self, endpoint, data):
        def _req():
            try:
                requests.post(f"{SERVER_URL}/api/{endpoint}", json=data, timeout=2)
                # print(f"📡 Gửi {endpoint}: {data}") # Bật dòng này nếu muốn xem log chi tiết
            except: 
                pass # Lỗi mạng thì bỏ qua, không làm lag camera
        threading.Thread(target=_req).start()

    def handle_attendance(self, name):
        if name in self.logged_attendance: return
        self.logged_attendance.add(name)
        print(f"✅ Điểm danh: {name}")
        self.send_api("attendance", {"name": name})

    def handle_violation(self, name, v_type):
        now = time.time()
        
        # 1. Sai đồng phục: Chỉ báo 1 lần duy nhất
        if "dong phuc" in v_type:
            if name in self.logged_uniform: return
            self.logged_uniform.add(name)
            print(f"⚠️ Vi phạm đồng phục: {name}")
            self.send_api("report", {"name": name, "type": v_type})
            
        # 2. Mất tập trung / Ngủ: Báo lại sau mỗi 30s
        else:
            key = f"{name}_{v_type}"
            if key in self.violation_cooldown and (now - self.violation_cooldown[key] < 30):
                return
            self.violation_cooldown[key] = now
            print(f"⚠️ Vi phạm hành vi: {name} - {v_type}")
            self.send_api("report", {"name": name, "type": v_type})

    # --- LOGIC NHẬN DIỆN ---
    def check_focus(self, landmarks):
        """Kiểm tra Mất tập trung (Mũi lệch khỏi tâm 2 mắt)"""
        x_re = landmarks[4]; x_le = landmarks[6]; x_nose = landmarks[8]
        eye_dist = abs(x_le - x_re)
        offset = abs(x_nose - (x_re + x_le) / 2)
        return (offset / eye_dist) > 0.5 if eye_dist > 0 else False

    def check_sleep(self, y, h):
        """Kiểm tra Ngủ (Đầu thấp dưới 60% khung hình)"""
        return y > h * 0.6

    def check_uniform(self, frame, box):
        x, y, w, h = box
        roi_y = min(y + h, frame.shape[0])
        roi_y_end = min(y + h + 80, frame.shape[0])
        if roi_y >= roi_y_end: return "unknown"
        
        roi = frame[roi_y:roi_y_end, max(0, x):min(x+w, frame.shape[1])]
        if roi.size == 0: return "unknown"

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 0, 168]), np.array([172, 111, 255]))
        return "white" if cv2.countNonZero(mask) / mask.size > 0.3 else "other"

    def run(self):
        print("📷 Camera đang chạy... (Nhấn 'q' để thoát)")
        
        while self.running:
            ret, frame = self.cap.read()
            if not ret: break

            h, w = frame.shape[:2]
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(frame)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if faces is not None:
                for f in faces:
                    box = list(map(int, f[:4]))
                    
                    # 1. Nhận diện
                    name = "Unknown"
                    roi = gray[box[1]:box[1]+box[3], box[0]:box[0]+box[2]]
                    if roi.size > 0:
                        try:
                            label, conf = self.recognizer.predict(cv2.resize(roi, (200, 200)))
                            if conf < 85: name = self.labels.get(label, "Unknown")
                        except: pass

                    if name != "Unknown":
                        self.handle_attendance(name)
                        
                        violation = ""
                        # 2. Check lỗi
                        if self.check_focus(f): 
                            violation = "Mat tap trung"
                        elif self.check_sleep(box[1], h):
                            violation = "Ngu gat"
                        elif self.check_uniform(frame, box) != "white":
                            # Mặc định ai cũng phải mặc áo trắng
                            violation = "Sai dong phuc"

                        # Gửi báo cáo (Nếu có lỗi)
                        if violation:
                            self.handle_violation(name, violation)

                        # Vẽ
                        color = (0,0,255) if violation else (0,255,0)
                        cv2.rectangle(frame, (box[0], box[1]), (box[0]+box[2], box[1]+box[3]), color, 2)
                        cv2.putText(frame, f"{name} {violation}", (box[0], box[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            cv2.imshow("AI Monitor", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'): break
            
        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    try:
        SmartMonitor().run()
    except Exception as e:
        print(f"❌ Lỗi: {e}")
