const express = require("express");
const http = require("http");
const socketIo = require("socket.io");
const bodyParser = require("body-parser");
const path = require("path");
const fs = require("fs");
const cors = require("cors");

const app = express();
const server = http.createServer(app);

// Cấu hình Socket.IO
const io = socketIo(server, {
    cors: {
        origin: "*",
        methods: ["GET", "POST"],
        credentials: true
    },
    transports: ['websocket', 'polling']
});

// Middleware
app.use(cors({
    origin: "*",
    methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allowedHeaders: ["Content-Type", "Authorization", "X-Session-Id"]
}));

app.use(bodyParser.json({ limit: '50mb' }));
app.use(bodyParser.urlencoded({ limit: '50mb', extended: true }));
app.use(express.static(path.join(__dirname, "../frontend")));

// Thêm middleware để log request
app.use((req, res, next) => {
    console.log(`[${new Date().toISOString()}] ${req.method} ${req.url}`);
    if (req.method === 'POST' && req.body) {
        console.log('Body:', JSON.stringify(req.body));
    }
    next();
});

// Data storage
const DATA_DIR = path.join(__dirname, "data");
const LOGS_FILE = path.join(DATA_DIR, "logs.json");
const ATTENDANCE_FILE = path.join(DATA_DIR, "attendance.json");

// Tạo thư mục data nếu chưa có
if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
}

// Load data
let logs = [];
let attendance = [];
let ledStatus = { color: "off", updatedAt: new Date() };
let currentTemp = 25.0;
let currentHum = 60.0;

function loadData() {
    try {
        if (fs.existsSync(LOGS_FILE)) {
            const logsData = fs.readFileSync(LOGS_FILE, 'utf8');
            logs = JSON.parse(logsData);
            console.log(`✅ Đã load ${logs.length} logs`);
        }
        if (fs.existsSync(ATTENDANCE_FILE)) {
            const attData = fs.readFileSync(ATTENDANCE_FILE, 'utf8');
            attendance = JSON.parse(attData);
            console.log(`✅ Đã load ${attendance.length} attendance records`);
        }
    } catch (err) {
        console.error("❌ Lỗi load data:", err);
        logs = [];
        attendance = [];
    }
}

function saveData() {
    try {
        fs.writeFileSync(LOGS_FILE, JSON.stringify(logs, null, 2));
        fs.writeFileSync(ATTENDANCE_FILE, JSON.stringify(attendance, null, 2));
        console.log(`💾 Đã lưu data: ${logs.length} logs, ${attendance.length} attendance`);
    } catch (err) {
        console.error("❌ Lỗi save data:", err);
    }
}

// Sessions
const sessions = new Map();

function generateSessionId() {
    return Math.random().toString(36).substring(2) + Date.now().toString(36);
}

setInterval(() => {
    const now = Date.now();
    for (const [sessionId, session] of sessions.entries()) {
        if (now - session.createdAt > 24 * 60 * 60 * 1000) {
            sessions.delete(sessionId);
        }
    }
}, 60 * 60 * 1000);

/* ========== AUTHENTICATION ========== */

app.post("/api/login", (req, res) => {
    try {
        console.log("🔐 Login attempt:", req.body);
        const { username, password } = req.body;
        
        if (!username || !password) {
            return res.json({ 
                success: false, 
                message: "Thiếu thông tin đăng nhập" 
            });
        }
        
        if (username === "admin" && password === "admin") {
            const sessionId = generateSessionId();
            sessions.set(sessionId, { 
                username, 
                createdAt: Date.now() 
            });
            
            console.log(`✅ Admin logged in: ${username}`);
            return res.json({ 
                success: true, 
                sessionId,
                username 
            });
        } else {
            console.log(`❌ Login failed: ${username}`);
            return res.json({ 
                success: false, 
                message: "Sai tài khoản hoặc mật khẩu" 
            });
        }
    } catch (err) {
        console.error("Login error:", err);
        res.status(500).json({ 
            success: false, 
            message: "Lỗi server" 
        });
    }
});

app.post("/api/logout", (req, res) => {
    try {
        const sessionId = req.headers['x-session-id'];
        if (sessionId) {
            sessions.delete(sessionId);
            console.log("👋 Admin logged out");
        }
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ success: false });
    }
});

app.get("/api/auth/check", (req, res) => {
    try {
        const sessionId = req.headers['x-session-id'];
        const session = sessions.get(sessionId);
        
        if (session) {
            if (Date.now() - session.createdAt > 24 * 60 * 60 * 1000) {
                sessions.delete(sessionId);
                res.json({ authenticated: false });
            } else {
                res.json({ 
                    authenticated: true, 
                    username: session.username 
                });
            }
        } else {
            res.json({ authenticated: false });
        }
    } catch (err) {
        res.json({ authenticated: false });
    }
});

/* ========== SOCKET.IO ========== */

io.on("connection", (socket) => {
    console.log(`🟢 Client connected: ${socket.id}`);
    
    // Send initial state
    socket.emit("init_state", {
        temp: currentTemp,
        hum: currentHum,
        led: ledStatus.color,
        clients: io.engine.clientsCount
    });
    
    // LED control
    socket.on("control_led", (data) => {
        try {
            console.log("💡 LED control request:", data);
            
            if (!data || !data.color) {
                return socket.emit("error", { message: "Thiếu thông tin màu LED" });
            }
            
            const oldColor = ledStatus.color;
            ledStatus = {
                color: data.color,
                updatedAt: new Date()
            };
            
            // Broadcast to all clients
            io.emit("led_command", data.color);
            console.log(`💡 LED changed: ${oldColor} → ${data.color} by ${socket.id}`);
            
            // Save log
            const log = {
                id: logs.length + 1,
                type: "led_control",
                message: `LED changed to ${data.color}`,
                color: data.color,
                timestamp: new Date().toISOString()
            };
            logs.unshift(log);
            if (logs.length > 500) logs.pop();
            
        } catch (err) {
            console.error("Socket control_led error:", err);
            socket.emit("error", { message: "Lỗi điều khiển LED" });
        }
    });
    
    socket.on("disconnect", (reason) => {
        console.log(`🔴 Client disconnected: ${socket.id} - ${reason}`);
    });
});

/* ========== PUBLIC API (ESP & AI) ========== */

// ESP gửi dữ liệu môi trường - FIXED
app.post("/api/env", (req, res) => {
    try {
        console.log("🌡️ Nhận dữ liệu môi trường:", req.body);
        
        const { temp, hum } = req.body;
        
        if (temp === undefined || hum === undefined) {
            console.error("❌ Thiếu dữ liệu temp hoặc hum");
            return res.status(400).json({ 
                error: "Thiếu dữ liệu temp hoặc hum",
                status: "error"
            });
        }
        
        currentTemp = parseFloat(temp);
        currentHum = parseFloat(hum);
        
        console.log(`✅ Đã nhận: Nhiệt độ=${currentTemp}°C, Độ ẩm=${currentHum}%`);
        
        // Tạo log
        const log = {
            id: logs.length + 1,
            type: "env",
            message: `Nhiệt độ: ${currentTemp}°C, Độ ẩm: ${currentHum}%`,
            temp: currentTemp,
            hum: currentHum,
            timestamp: new Date().toISOString()
        };
        
        logs.unshift(log);
        if (logs.length > 500) logs.pop();
        
        // Gửi qua socket
        io.emit("sensor_data", { 
            temp: currentTemp, 
            hum: currentHum, 
            timestamp: log.timestamp 
        });
        
        // Lưu data
        saveData();
        
        res.json({ 
            status: "ok",
            message: "Dữ liệu đã nhận",
            temp: currentTemp,
            hum: currentHum
        });
        
    } catch (err) {
        console.error("❌ Lỗi /api/env:", err);
        res.status(500).json({ 
            error: "Lỗi server",
            message: err.message,
            status: "error"
        });
    }
});

// AI gửi báo cáo vi phạm
app.post("/api/report", (req, res) => {
    try {
        console.log("🚨 Nhận báo cáo vi phạm:", req.body);
        
        const { name, type } = req.body;

        if (!name || !type) {
            return res.status(400).json({ 
                error: "Thiếu name hoặc type",
                status: "error"
            });
        }

        let level = "info";
        if (type.includes("Ngu") || type.includes("Quay")) {
            level = "red";
        } else if (type.includes("dong phuc") || type.includes("Đồng phục")) {
            level = "green";

            // Kiểm tra nếu đã báo hôm nay
            const today = new Date().toISOString().split('T')[0];
            const alreadyReported = logs.some(l =>
                l.name === name &&
                l.type === type &&
                l.timestamp.startsWith(today)
            );
            if (alreadyReported) {
                console.log(`⏭️ ${name} đã báo '${type}' hôm nay`);
                return res.json({
                    status: "already_reported",
                    message: `${name} đã báo '${type}' hôm nay`
                });
            }
        }

        const log = {
            id: logs.length + 1,
            type: type,
            level: level,
            message: `${name}: ${type}`,
            name: name,
            timestamp: new Date().toISOString()
        };

        logs.unshift(log);
        if (logs.length > 500) logs.pop();

        io.emit("violation", log);
        console.log(`🚨 Vi phạm: ${name} - ${type} (${level})`);

        saveData();
        res.json({ 
            status: "ok",
            message: "Báo cáo đã nhận"
        });
        
    } catch (err) {
        console.error("❌ Lỗi /api/report:", err);
        res.status(500).json({ 
            error: "Lỗi server",
            status: "error"
        });
    }
});

// AI gửi điểm danh
app.post("/api/attendance", (req, res) => {
    try {
        console.log("✅ Nhận điểm danh:", req.body);
        
        const { name } = req.body;
        
        if (!name) {
            return res.status(400).json({ 
                error: "Thiếu tên",
                status: "error"
            });
        }
        
        // Kiểm tra đã điểm danh hôm nay chưa
        const today = new Date().toISOString().split('T')[0];
        const alreadyAttended = attendance.some(a => 
            a.name === name && a.timestamp.startsWith(today)
        );
        
        if (alreadyAttended) {
            console.log(`⏭️ ${name} đã điểm danh rồi`);
            return res.json({ 
                status: "already_attended",
                message: `${name} đã điểm danh hôm nay`
            });
        }
        
        const record = {
            id: attendance.length + 1,
            name: name,
            timestamp: new Date().toISOString()
        };
        
        attendance.push(record);
        
        io.emit("attendance", { 
            name: name, 
            timestamp: record.timestamp 
        });
        
        console.log(`✅ Điểm danh thành công: ${name}`);
        
        saveData();
        res.json({ 
            status: "ok",
            message: "Điểm danh thành công"
        });
        
    } catch (err) {
        console.error("❌ Lỗi /api/attendance:", err);
        res.status(500).json({ 
            error: "Lỗi server",
            status: "error"
        });
    }
});

/* ========== ESP SPECIFIC API ========== */

// ESP lấy trạng thái LED - FIXED
app.get("/api/esp/led", (req, res) => {
    try {
        console.log("💡 ESP request LED status");
        res.json({
            color: ledStatus.color,
            updatedAt: ledStatus.updatedAt,
            status: "ok"
        });
    } catch (err) {
        console.error("❌ Lỗi /api/esp/led:", err);
        res.status(500).json({ 
            error: "Server error",
            status: "error"
        });
    }
});

// ESP lấy cảnh báo cuối cùng - FIXED
app.get("/api/esp/last-alert", (req, res) => {
    try {
        console.log("📢 ESP request last alert");
        
        // Tìm cảnh báo gần nhất (không phải env)
        const alerts = logs.filter(l => l.type !== "env" && l.type !== "led_control");
        const lastAlert = alerts[0];
        
        if (!lastAlert) {
            console.log("📭 Không có cảnh báo nào");
            return res.json({ 
                message: "No alert",
                level: "info",
                status: "ok"
            });
        }
        
        console.log(`📢 Gửi cảnh báo: ${lastAlert.message}`);
        
        // Format message cho LCD
        let displayMessage = lastAlert.message;
        if (displayMessage.length > 32) {
            displayMessage = displayMessage.substring(0, 32);
        }
        
        res.json({
            message: displayMessage,
            level: lastAlert.level || "info",
            timestamp: lastAlert.timestamp,
            status: "ok"
        });
        
    } catch (err) {
        console.error("❌ Lỗi /api/esp/last-alert:", err);
        res.status(500).json({ 
            error: "Server error",
            status: "error"
        });
    }
});

// Health check cho ESP - FIXED
app.get("/api/esp/health", (req, res) => {
    try {
        console.log("🏥 ESP health check");
        res.json({
            status: "online",
            server_time: new Date().toISOString(),
            led_status: ledStatus.color,
            last_temp: currentTemp,
            last_hum: currentHum,
            total_logs: logs.length,
            connected_clients: io.engine.clientsCount
        });
    } catch (err) {
        console.error("❌ Lỗi /api/esp/health:", err);
        res.status(500).json({ 
            error: "Server error",
            status: "error"
        });
    }
});

/* ========== PROTECTED API (WEB ADMIN) ========== */

// Auth middleware
const requireAuth = (req, res, next) => {
    try {
        const sessionId = req.headers['x-session-id'];
        
        if (!sessionId) {
            console.log("❌ Không tìm thấy session ID");
            return res.status(401).json({ 
                error: "Không tìm thấy session" 
            });
        }
        
        const session = sessions.get(sessionId);
        
        if (!session) {
            console.log("❌ Session không hợp lệ:", sessionId);
            return res.status(401).json({ 
                error: "Session không hợp lệ" 
            });
        }
        
        // Check session expiration
        if (Date.now() - session.createdAt > 24 * 60 * 60 * 1000) {
            sessions.delete(sessionId);
            return res.status(401).json({ 
                error: "Session đã hết hạn" 
            });
        }
        
        req.session = session;
        next();
    } catch (err) {
        console.error("Auth middleware error:", err);
        res.status(500).json({ error: "Lỗi xác thực" });
    }
};

// Điều khiển LED từ web
app.post("/api/iot/led", requireAuth, (req, res) => {
    try {
        const { color } = req.body;
        console.log("💡 Web LED control:", color, "by", req.session.username);
        
        if (!color || !["red", "green", "yellow", "off"].includes(color)) {
            return res.status(400).json({ 
                error: "Màu LED không hợp lệ" 
            });
        }
        
        const oldColor = ledStatus.color;
        ledStatus = {
            color: color,
            updatedAt: new Date()
        };
        
        io.emit("led_command", color);
        console.log(`💡 LED set: ${oldColor} → ${color} by ${req.session.username}`);
        
        res.json({ 
            ok: true, 
            color: color,
            updatedAt: ledStatus.updatedAt
        });
    } catch (err) {
        console.error("❌ LED control error:", err);
        res.status(500).json({ error: "Lỗi server" });
    }
});

// Lấy trạng thái LED
app.get("/api/iot/led/status", requireAuth, (req, res) => {
    res.json(ledStatus);
});

// Lấy logs với phân trang
app.get("/api/logs", requireAuth, (req, res) => {
    try {
        const page = parseInt(req.query.page) || 1;
        const limit = parseInt(req.query.limit) || 50;
        const type = req.query.type;
        const search = req.query.search;
        
        let filteredLogs = [...logs];
        
        if (type && type !== "all") {
            filteredLogs = filteredLogs.filter(log => log.type === type);
        }
        
        if (search) {
            const searchLower = search.toLowerCase();
            filteredLogs = filteredLogs.filter(log => 
                log.message.toLowerCase().includes(searchLower) ||
                (log.name && log.name.toLowerCase().includes(searchLower))
            );
        }
        
        const startIndex = (page - 1) * limit;
        const endIndex = startIndex + limit;
        const paginatedLogs = filteredLogs.slice(startIndex, endIndex);
        
        res.json({
            logs: paginatedLogs,
            total: filteredLogs.length,
            page: page,
            totalPages: Math.ceil(filteredLogs.length / limit)
        });
    } catch (err) {
        console.error("❌ Get logs error:", err);
        res.status(500).json({ error: "Lỗi server" });
    }
});

// Lấy danh sách điểm danh hôm nay
app.get("/api/attendance/list", requireAuth, (req, res) => {
    try {
        const today = new Date().toISOString().split('T')[0];
        const todayAttendance = attendance.filter(a => 
            a.timestamp.startsWith(today)
        );
        
        const byHour = {};
        todayAttendance.forEach(record => {
            const hour = record.timestamp.split('T')[1].substring(0, 2);
            if (!byHour[hour]) byHour[hour] = [];
            byHour[hour].push(record);
        });
        
        res.json({
            records: todayAttendance,
            byHour: byHour,
            total: todayAttendance.length
        });
    } catch (err) {
        console.error("❌ Attendance list error:", err);
        res.status(500).json({ error: "Lỗi server" });
    }
});

// Lấy danh sách sinh viên
app.get("/api/students", requireAuth, (req, res) => {
    try {
        const facesDir = path.join(__dirname, "../ai_processor/faces_db");
        
        if (!fs.existsSync(facesDir)) {
            return res.json([]);
        }
        
        const students = fs.readdirSync(facesDir)
            .filter(name => {
                try {
                    const stat = fs.statSync(path.join(facesDir, name));
                    return stat.isDirectory();
                } catch (err) {
                    return false;
                }
            })
            .map(name => {
                try {
                    const studentDir = path.join(facesDir, name);
                    const images = fs.readdirSync(studentDir)
                        .filter(f => f.match(/\.(jpg|jpeg|png|webp)$/i));
                    
                    return {
                        name: name,
                        imageCount: images.length,
                        lastModified: fs.statSync(studentDir).mtime
                    };
                } catch (err) {
                    return { name: name, imageCount: 0, error: true };
                }
            })
            .filter(student => !student.error);
        
        res.json(students);
    } catch (err) {
        console.error("❌ Students list error:", err);
        res.json([]);
    }
});

// Thống kê
app.get("/api/stats", requireAuth, (req, res) => {
    try {
        const today = new Date().toISOString().split('T')[0];
        const todayAttendance = attendance.filter(a => 
            a.timestamp.startsWith(today)
        );
        
        const uniqueStudents = [...new Set(todayAttendance.map(a => a.name))];
        
        const violations = logs.filter(l => 
            l.type !== 'env' && 
            l.type !== 'led_control' &&
            l.timestamp.startsWith(today)
        );
        
        const violationTypes = {};
        violations.forEach(v => {
            violationTypes[v.type] = (violationTypes[v.type] || 0) + 1;
        });
        
        res.json({
            total_logs: logs.length,
            total_attendance: attendance.length,
            students_today: uniqueStudents.length,
            attendance_today: todayAttendance.length,
            violations_today: violations.length,
            violation_types: violationTypes,
            current_temp: currentTemp,
            current_hum: currentHum,
            led_status: ledStatus.color,
            online_clients: io.engine.clientsCount,
            server_uptime: process.uptime()
        });
    } catch (err) {
        console.error("❌ Stats error:", err);
        res.status(500).json({ error: "Lỗi server" });
    }
});

// Reset data
app.post("/api/reset", requireAuth, (req, res) => {
    try {
        const { type } = req.body;
        console.log("🗑️ Reset request:", type, "by", req.session.username);
        
        if (type === "logs") {
            const count = logs.length;
            logs = [];
            fs.writeFileSync(LOGS_FILE, JSON.stringify([], null, 2));
            console.log(`🗑️ Logs cleared: ${count} records`);
            
            io.emit("data_reset", { type: "logs" });
            res.json({ 
                reset: true, 
                type: "logs", 
                cleared: count 
            });
            
        } else if (type === "attendance") {
            const count = attendance.length;
            attendance = [];
            fs.writeFileSync(ATTENDANCE_FILE, JSON.stringify([], null, 2));
            console.log(`🗑️ Attendance cleared: ${count} records`);
            
            io.emit("data_reset", { type: "attendance" });
            res.json({ 
                reset: true, 
                type: "attendance", 
                cleared: count 
            });
            
        } else {
            const logCount = logs.length;
            const attCount = attendance.length;
            
            logs = [];
            attendance = [];
            saveData();
            
            console.log(`🗑️ All data cleared: ${logCount} logs, ${attCount} attendance`);
            
            io.emit("data_reset", { type: "all" });
            res.json({ 
                reset: true, 
                type: "all", 
                cleared: { logs: logCount, attendance: attCount }
            });
        }
    } catch (err) {
        console.error("❌ Reset error:", err);
        res.status(500).json({ error: "Lỗi server" });
    }
});

// Download data
app.get("/api/export", requireAuth, (req, res) => {
    try {
        const type = req.query.type || "logs";
        const today = new Date().toISOString().split('T')[0];
        
        let data, filename;
        
        if (type === "attendance") {
            data = attendance;
            filename = `attendance_${today}.json`;
        } else if (type === "logs") {
            data = logs;
            filename = `logs_${today}.json`;
        } else {
            data = { logs, attendance };
            filename = `iot_system_${today}.json`;
        }
        
        res.setHeader('Content-Type', 'application/json');
        res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
        res.send(JSON.stringify(data, null, 2));
        
    } catch (err) {
        console.error("❌ Export error:", err);
        res.status(500).json({ error: "Lỗi export" });
    }
});

/* ========== FRONTEND ROUTES ========== */

app.get("/", (req, res) => {
    res.sendFile(path.join(__dirname, "../frontend", "login.html"));
});

app.get("/dashboard", (req, res) => {
    res.sendFile(path.join(__dirname, "../frontend", "dashboard.html"));
});

app.get("/logs", requireAuth, (req, res) => {
    res.sendFile(path.join(__dirname, "../frontend", "logs.html"));
});

app.get("/attendance", requireAuth, (req, res) => {
    res.sendFile(path.join(__dirname, "../frontend", "attendance.html"));
});

app.get("/control", requireAuth, (req, res) => {
    res.sendFile(path.join(__dirname, "../frontend", "control.html"));
});

// Health check endpoint
app.get("/health", (req, res) => {
    res.json({
        status: "running",
        uptime: process.uptime(),
        logs: logs.length,
        attendance: attendance.length,
        connections: io.engine.clientsCount,
        memory: process.memoryUsage(),
        led_status: ledStatus,
        current_temp: currentTemp,
        current_hum: currentHum,
        node_version: process.version,
        platform: process.platform
    });
});

// Test endpoint cho ESP
app.get("/api/test", (req, res) => {
    res.json({
        message: "Server is working!",
        timestamp: new Date().toISOString(),
        led_status: ledStatus.color
    });
});

// 404 handler
app.use((req, res) => {
    console.log(`❌ 404 Not Found: ${req.method} ${req.url}`);
    res.status(404).json({ 
        error: "Not found",
        path: req.path,
        method: req.method
    });
});

// Error handler
app.use((err, req, res, next) => {
    console.error("❌ Server error:", err);
    res.status(500).json({ 
        error: "Internal server error",
        message: err.message,
        stack: process.env.NODE_ENV === 'development' ? err.stack : undefined
    });
});

// Load data on startup
loadData();

// Auto-save every 5 minutes
setInterval(saveData, 5 * 60 * 1000);

// Save on exit
process.on('SIGINT', () => {
    console.log("\n💾 Saving data before exit...");
    saveData();
    process.exit(0);
});

process.on('SIGTERM', () => {
    console.log("\n💾 Saving data before termination...");
    saveData();
    process.exit(0);
});

// Start server
const PORT = process.env.PORT || 3000;
server.listen(PORT, "0.0.0.0", () => {
    console.log("\n" + "=".repeat(50));
    console.log("🚀 IOT SERVER STARTED");
    console.log("=".repeat(50));
    console.log(`📡 Server running: http://localhost:${PORT}`);
    console.log(`🌐 Network access: http://${getLocalIP()}:${PORT}`);
    console.log("=".repeat(50));
    console.log(`🔐 Login: admin / admin`);
    console.log(`📁 Data directory: ${DATA_DIR}`);
    console.log(`📊 Initial stats:`);
    console.log(`   Logs: ${logs.length} records`);
    console.log(`   Attendance: ${attendance.length} records`);
    console.log(`💡 LED status: ${ledStatus.color}`);
    console.log("=".repeat(50));
});

// Helper function để lấy IP
function getLocalIP() {
    const interfaces = require('os').networkInterfaces();
    for (const name of Object.keys(interfaces)) {
        for (const iface of interfaces[name]) {
            if (iface.family === 'IPv4' && !iface.internal) {
                return iface.address;
            }
        }
    }
    return 'localhost';
}
