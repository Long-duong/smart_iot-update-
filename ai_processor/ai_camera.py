import cv2
import numpy as np
import requests
import os
import time
import signal
import sys

# ================= CẤU HÌNH =================
NODE_API = "http://localhost:3000/api"

YUNET_MODEL = "face_detection_yunet_2023mar.onnx"
DATASET_DIR = "faces_db"

SEND_INTERVAL = 3        # giây, chống spam report
ATTENDANCE_INTERVAL = 10

# ============================================


class AICamera:
    def __init__(self):
        print("🚀 AI Camera khởi động (Server-centered)")

        self.running = True
        signal.signal(signal.SIGINT, self.stop)

        self.last_report_time = 0
        self.last_attendance_time = {}
        self.attended_today = set()

        self.download_model_if_missing()

        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.detector = cv2.FaceDetectorYN.create(
            YUNET_MODEL, "", (320, 320), 0.7, 0.3, 5000
        )

        self.recognizer = cv2.face.LBPHFaceRecognizer_create()
        self.labels = {}
        self.load_trained_data()

    # ================= SYSTEM =================
    def stop(self, sig, frame):
        print("\n🛑 Dừng AI Camera")
        self.running = False

    def cleanup(self):
        if self.cap.isOpened():
            self.cap.release()
        cv2.destroyAllWindows()
        sys.exit(0)

    # ================= MODEL =================
    def download_model_if_missing(self):
        if not os.path.exists(YUNET_MODEL):
            print("⬇️ Tải YuNet model...")
            url = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
            r = requests.get(url, timeout=10)
            with open(YUNET_MODEL, "wb") as f:
                f.write(r.content)

    def load_trained_data(self):
        if not os.path.exists(DATASET_DIR):
            os.makedirs(DATASET_DIR)
            return

        faces, ids = [], []
        idx = 0

        for name in os.listdir(DATASET_DIR):
            person_dir = os.path.join(DATASET_DIR, name)
            if not os.path.isdir(person_dir):
                continue

            self.labels[idx] = name
            for img_name in os.listdir(person_dir):
                if img_name.lower().endswith(("jpg", "png")):
                    img = cv2.imread(os.path.join(person_dir, img_name), cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        img = cv2.resize(img, (200, 200))
                        faces.append(img)
                        ids.append(idx)
            idx += 1

        if faces:
            self.recognizer.train(faces, np.array(ids))
            print(f"✅ Load {len(self.labels)} sinh viên")

    # ================= API =================
    def send_report(self, name, violation):
        now = time.time()
        if now - self.last_report_time < SEND_INTERVAL:
            return

        try:
            requests.post(
                f"{NODE_API}/report",
                json={"name": name, "type": violation},
                timeout=1
            )
            self.last_report_time = now
            print(f"🚨 {name}: {violation}")
        except:
            pass

    def send_attendance(self, name):
        now = time.time()
        last = self.last_attendance_time.get(name, 0)

        if now - last < ATTENDANCE_INTERVAL:
            return

        try:
            requests.post(
                f"{NODE_API}/attendance",
                json={"name": name},
                timeout=1
            )
            self.last_attendance_time[name] = now
            print(f"✅ Điểm danh: {name}")
        except:
            pass

    # ================= UNIFORM =================
    def check_uniform(self, frame, box):
        x, y, w, h = box
        chest_y = y + int(h * 0.6)
        chest_h = int(h * 0.3)

        if chest_y + chest_h > frame.shape[0]:
            return True

        chest = frame[chest_y:chest_y + chest_h, x:x + w]
        hsv = cv2.cvtColor(chest, cv2.COLOR_BGR2HSV)

        lower = np.array([90, 50, 50])   # xanh đồng phục
        upper = np.array([130, 255, 255])

        mask = cv2.inRange(hsv, lower, upper)
        ratio = cv2.countNonZero(mask) / (chest.size / 3)

        return ratio > 0.25

    # ================= MAIN =================
    def run(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                break

            h, w = frame.shape[:2]
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(frame)

            if faces is not None:
                for f in faces:
                    box = list(map(int, f[:4]))
                    name = "Unknown"

                    roi = frame[box[1]:box[1]+box[3], box[0]:box[0]+box[2]]
                    if roi.size > 0:
                        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                        g = cv2.resize(g, (200, 200))
                        label, conf = self.recognizer.predict(g)
                        if conf < 85:
                            name = self.labels[label]

                    if name != "Unknown":
                        self.send_attendance(name)

                    violation = ""

                    ratio = box[2] / box[3]
                    if ratio < 0.65:
                        violation = "Gian lan (Quay dau)"
                    elif box[1] > h * 0.65:
                        violation = "Ngu gat"
                    elif name != "Unknown" and not self.check_uniform(frame, box):
                        violation = "Sai dong phuc"

                    color = (0, 255, 0)
                    if violation:
                        color = (0, 0, 255)
                        self.send_report(name, violation)

                    cv2.rectangle(frame, (box[0], box[1]),
                                  (box[0]+box[2], box[1]+box[3]), color, 2)
                    cv2.putText(frame, name, (box[0], box[1]-10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            cv2.imshow("AI Camera System", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                self.running = False

        self.cleanup()


if __name__ == "__main__":
    AICamera().run()
