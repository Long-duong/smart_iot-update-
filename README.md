ter 1: node server1.js
ter 2: python aa.py
http://localhost:3000


🎓 Smart Classroom Monitoring System
(Hệ thống Giám sát Lớp học Thông minh tích hợp AI & IoT)

📖 Giới thiệu
Dự án Smart Classroom là giải pháp tự động hóa việc quản lý lớp học, sử dụng:

AI (Computer Vision): Điểm danh khuôn mặt, phát hiện hành vi bất thường (ngủ gật, mất tập trung), và kiểm tra đồng phục.

IoT (ESP8266): Giám sát nhiệt độ/độ ẩm và cảnh báo bằng đèn LED/LCD thời gian thực.

Web Server: Dashboard quản lý tập trung, lưu trữ lịch sử và điều khiển hệ thống.

🚀 Tính năng nổi bật
1. AI Camera (Python Client)
✅ Điểm danh khuôn mặt: Sử dụng model YuNet và LBPH để nhận diện sinh viên.

🧠 Phát hiện hành vi thông minh (Landmarks):

Mất tập trung: Tính toán độ lệch của Mũi so với trung tâm 2 Mắt để phát hiện quay đầu chính xác.

Ngủ gật: Phát hiện vị trí đầu cúi thấp xuống mặt bàn.

👕 Kiểm tra đồng phục: Tự động phát hiện màu áo (mặc định yêu cầu áo Trắng).

🛡️ Cơ chế Anti-Spam:

Điểm danh & Lỗi đồng phục: Chỉ báo 1 lần duy nhất mỗi phiên.

Lỗi hành vi: Có thời gian chờ (cooldown) 30 giây để tránh spam thông báo.

2. IoT Device (ESP8266)
🌡️ Giám sát môi trường: Đọc cảm biến DHT11 và gửi dữ liệu lên Server mỗi 5 giây.

💡 Hệ thống đèn báo:

🟢 Xanh: Hệ thống hoạt động bình thường.

🟡 Vàng: Cảnh báo nhiệt độ cao (>35°C) hoặc sinh viên ngủ gật.

🔴 Đỏ: Cảnh báo gian lận/mất tập trung hoặc lỗi kết nối.

📟 Màn hình LCD: Hiển thị Nhiệt độ & Độ ẩm hiện tại.

3. Web Dashboard (Node.js)
🔌 API Gateway: Trung gian kết nối giữa AI và IoT (ESP8266 không cần kết nối trực tiếp với Python).

📊 Dashboard: Hiển thị sĩ số, danh sách vắng, log vi phạm theo thời gian thực.

🗄️ Lưu trữ: Dữ liệu điểm danh và vi phạm được lưu vào file JSON.
