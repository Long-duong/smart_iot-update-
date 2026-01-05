import requests
import time

# ================= CẤU HÌNH =================
BACKEND_URL = "http://localhost:3000/api/report"  # Đúng endpoint
API_KEY = "so_secret_123"                         # Phải khớp với server.js
HEADERS = {
    "x-api-key": API_KEY,
    "Content-Type": "application/json"
}

# Biến toàn cục chống spam (tương tự code cũ)
last_report_time = {}  # {name_violation_type: timestamp}

def send_to_backend(name: str, violation_type: str, min_interval=3.0) -> bool:
    """
    Gửi báo cáo vi phạm lên backend Node.js
    Trả về True nếu gửi thành công, False nếu thất bại
    
    Args:
        name: Tên học sinh
        violation_type: Loại vi phạm (ví dụ: "Ngu gat", "Sai dong phuc")
        min_interval: Khoảng cách tối thiểu giữa 2 lần gửi cùng loại (giây)
    
    Returns:
        bool: Thành công hay không
    """
    # Tạo key chống spam: kết hợp name + type để tránh spam cùng học sinh cùng loại
    spam_key = f"{name}_{violation_type}"
    
    current_time = time.time()
    if spam_key in last_report_time and (current_time - last_report_time[spam_key]) < min_interval:
        print(f"⏳ Chống spam: {name} - {violation_type} (chưa đủ {min_interval}s)")
        return False
    
    try:
        response = requests.post(
            BACKEND_URL,
            json={"name": name, "type": violation_type},
            headers=HEADERS,
            timeout=2.0  # Timeout 2 giây, tránh treo
        )
        
        if response.status_code == 200:
            last_report_time[spam_key] = current_time
            print(f"✅ Gửi báo cáo thành công: {name} → {violation_type}")
            return True
        else:
            print(f"❌ Lỗi từ server: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"⌛ Timeout khi gửi báo cáo: {name} - {violation_type}")
        return False
    except requests.exceptions.ConnectionError:
        print(f"🌐 Không kết nối được tới backend Node.js ({BACKEND_URL})")
        return False
    except Exception as e:
        print(f"🚨 Lỗi không xác định khi gửi báo cáo: {str(e)}")
        return False
