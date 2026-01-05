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
        methods: ["GET", "POST"]
    },
    transports: ['websocket', 'polling']
});

// Middleware
app.use(cors());
app.use(bodyParser.json());
app.use(express.static(path.join(__dirname, "../frontend")));

// Data storage
const DATA_DIR = path.join(__dirname, "data");
const LOGS_FILE = path.join(DATA_DIR, "logs.json");
const ATTENDANCE_FILE = path.join(DATA_DIR, "attendance.json");

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
            logs = JSON.parse(fs.readFileSync(LOGS_FILE, 'utf8'));
            console.log(`✅ Loaded ${logs.length} logs`);
        }
        if (fs.existsSync(ATTENDANCE_FILE)) {
            attendance = JSON.parse(fs.readFileSync(ATTENDANCE_FILE, 'utf8'));
            console.log(`✅ Loaded ${attendance.length} attendance records`);
        }
    } catch (err) {
        console.error("❌ Load error:", err);
        logs = [];
        attendance = [];
    }
}

function saveData() {
    try {
        fs.writeFileSync(LOGS_FILE, JSON.stringify(logs, null, 2));
        fs.writeFileSync(ATTENDANCE_FILE, JSON.stringify(attendance, null, 2));
    } catch (err) {
        console.error("❌ Save error:", err);
    }
}

// Sessions
const sessions = new Map();
function generateSessionId() {
    return Math.random().toString(36).substring(2) + Date.now().toString(36);
}

// Auto-clean sessions
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
        const { username, password } = req.body;
        
        if (!username || !password) {
            return res.json({ 
                success: false, 
                message: "Missing credentials" 
            });
        }
        
        if (username === "admin" && password === "admin") {
            const sessionId = generateSessionId();
            sessions.set(sessionId, { 
                username, 
                createdAt: Date.now() 
            });
            
            console.log(`✅ Login: ${username}`);
            res.json({ 
                success: true, 
                sessionId,
                username 
            });
        } else {
            res.json({ 
                success: false, 
                message: "Invalid credentials" 
            });
        }
    } catch (err) {
        res.status(500).json({ 
            success: false, 
            message: "Server error" 
        });
    }
});

app.post("/api/logout", (req, res) => {
    try {
        const sessionId = req.headers['x-session-id'];
        if (sessionId) sessions.delete(sessionId);
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ success: false });
    }
});

app.get("/api/auth/check", (req, res) => {
    try {
        const sessionId = req.headers['x-session-id'];
        const session = sessions.get(sessionId);
        
        if (session && Date.now() - session.createdAt < 24 * 60 * 60 * 1000) {
            res.json({ 
                authenticated: true, 
                username: session.username 
            });
        } else {
            if (sessionId) sessions.delete(sessionId);
            res.json({ authenticated: false });
        }
    } catch (err) {
        res.json({ authenticated: false });
    }
});

/* ========== SOCKET.IO ========== */
io.on("connection", (socket) => {
    console.log(`🟢 Client connected: ${socket.id}`);
    
    socket.emit("init_state", {
        temp: currentTemp,
        hum: currentHum,
        led: ledStatus.color
    });
    
    socket.on("control_led", (data) => {
        try {
            if (!data.color) return;
            
            ledStatus = {
                color: data.color,
                updatedAt: new Date()
            };
            
            io.emit("led_command", data.color);
            console.log(`💡 LED: ${data.color}`);
        } catch (err) {
            console.error("Socket error:", err);
        }
    });
    
    socket.on("disconnect", () => {
        console.log(`🔴 Client disconnected: ${socket.id}`);
    });
});

/* ========== PUBLIC API ========== */
app.post("/api/env", (req, res) => {
    try {
        const { temp, hum } = req.body;
        
        if (temp === undefined || hum === undefined) {
            return res.status(400).json({ 
                error: "Missing temp or hum" 
            });
        }
        
        currentTemp = parseFloat(temp);
        currentHum = parseFloat(hum);
        
        const log = {
            id: logs.length + 1,
            type: "env",
            message: `Temperature: ${currentTemp}°C, Humidity: ${currentHum}%`,
            temp: currentTemp,
            hum: currentHum,
            timestamp: new Date().toISOString()
        };
        
        logs.unshift(log);
        if (logs.length > 1000) logs.pop();
        
        io.emit("sensor_data", { 
            temp: currentTemp, 
            hum: currentHum, 
            timestamp: log.timestamp 
        });
        
        saveData();
        res.json({ 
            status: "ok",
            received: { temp: currentTemp, hum: currentHum }
        });
    } catch (err) {
        res.status(500).json({ error: "Server error" });
    }
});

app.post("/api/report", (req, res) => {
    try {
        const { name, type } = req.body;

        if (!name || !type) {
            return res.status(400).json({ error: "Missing name or type" });
        }

        let level = "info";
        if (type.includes("Ngu") || type.includes("Quay")) {
            level = "red";
        } else if (type.includes("dong phuc") || type.includes("Đồng phục")) {
            level = "green";
            
            const today = new Date().toISOString().split('T')[0];
            const alreadyReported = logs.some(l =>
                l.name === name &&
                l.type === type &&
                l.timestamp.startsWith(today)
            );
            if (alreadyReported) {
                return res.json({
                    status: "already_reported",
                    message: `${name} already reported '${type}' today`
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
        if (logs.length > 1000) logs.pop();

        io.emit("violation", log);
        console.log(`🚨 Violation: ${name} - ${type}`);

        saveData();
        res.json({ status: "ok" });
    } catch (err) {
        res.status(500).json({ error: "Server error" });
    }
});

app.post("/api/attendance", (req, res) => {
    try {
        const { name } = req.body;
        
        if (!name) {
            return res.status(400).json({ error: "Missing name" });
        }
        
        const today = new Date().toISOString().split('T')[0];
        const alreadyAttended = attendance.some(a => 
            a.name === name && a.timestamp.startsWith(today)
        );
        
        if (alreadyAttended) {
            return res.json({ 
                status: "already_attended",
                message: `${name} already attended today`
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
        
        saveData();
        res.json({ status: "ok" });
    } catch (err) {
        res.status(500).json({ error: "Server error" });
    }
});

/* ========== ESP SPECIFIC API ========== */
app.get("/api/esp/led", (req, res) => {
    try {
        res.json({
            color: ledStatus.color,
            updatedAt: ledStatus.updatedAt,
            status: "ok"
        });
    } catch (err) {
        res.status(500).json({ 
            error: "Server error",
            status: "error"
        });
    }
});

app.get("/api/esp/last-alert", (req, res) => {
    try {
        const alerts = logs.filter(l => l.type !== "env" && l.type !== "led_control");
        const lastAlert = alerts[0];
        
        if (!lastAlert) {
            return res.json({ 
                message: "No alert",
                level: "info",
                status: "ok"
            });
        }
        
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
        res.status(500).json({ 
            error: "Server error",
            status: "error"
        });
    }
});

app.get("/api/esp/health", (req, res) => {
    res.json({
        status: "online",
        server_time: new Date().toISOString(),
        led_status: ledStatus.color,
        last_temp: currentTemp,
        last_hum: currentHum,
        total_logs: logs.length
    });
});

// Test endpoint
app.get("/api/test", (req, res) => {
    res.json({
        message: "Server is working!",
        timestamp: new Date().toISOString(),
        led_status: ledStatus.color
    });
});

/* ========== PROTECTED API ========== */
const requireAuth = (req, res, next) => {
    try {
        const sessionId = req.headers['x-session-id'];
        const session = sessions.get(sessionId);
        
        if (!session) {
            return res.status(401).json({ error: "Invalid session" });
        }
        
        if (Date.now() - session.createdAt > 24 * 60 * 60 * 1000) {
            sessions.delete(sessionId);
            return res.status(401).json({ error: "Session expired" });
        }
        
        req.session = session;
        next();
    } catch (err) {
        res.status(500).json({ error: "Auth error" });
    }
};

app.post("/api/iot/led", requireAuth, (req, res) => {
    try {
        const { color } = req.body;
        
        if (!color || !["red", "green", "yellow", "off"].includes(color)) {
            return res.status(400).json({ error: "Invalid color" });
        }
        
        ledStatus = {
            color: color,
            updatedAt: new Date()
        };
        
        io.emit("led_command", color);
        console.log(`💡 LED set to ${color} by ${req.session.username}`);
        
        res.json({ 
            ok: true, 
            color: color,
            updatedAt: ledStatus.updatedAt
        });
    } catch (err) {
        res.status(500).json({ error: "Server error" });
    }
});

app.get("/api/iot/led/status", requireAuth, (req, res) => {
    res.json(ledStatus);
});

app.get("/api/logs", requireAuth, (req, res) => {
    try {
        const page = parseInt(req.query.page) || 1;
        const limit = parseInt(req.query.limit) || 50;
        
        const startIndex = (page - 1) * limit;
        const endIndex = startIndex + limit;
        const paginatedLogs = logs.slice(startIndex, endIndex);
        
        res.json({
            logs: paginatedLogs,
            total: logs.length,
            page: page,
            totalPages: Math.ceil(logs.length / limit)
        });
    } catch (err) {
        res.status(500).json({ error: "Server error" });
    }
});

app.get("/api/attendance/list", requireAuth, (req, res) => {
    try {
        const today = new Date().toISOString().split('T')[0];
        const todayAttendance = attendance.filter(a => 
            a.timestamp.startsWith(today)
        );
        
        res.json({
            records: todayAttendance,
            total: todayAttendance.length
        });
    } catch (err) {
        res.status(500).json({ error: "Server error" });
    }
});

app.get("/api/stats", requireAuth, (req, res) => {
    try {
        const today = new Date().toISOString().split('T')[0];
        const todayAttendance = attendance.filter(a => 
            a.timestamp.startsWith(today)
        );
        
        const violations = logs.filter(l => 
            l.type !== 'env' && 
            l.type !== 'led_control' &&
            l.timestamp.startsWith(today)
        );
        
        res.json({
            total_logs: logs.length,
            total_attendance: attendance.length,
            attendance_today: todayAttendance.length,
            violations_today: violations.length,
            current_temp: currentTemp,
            current_hum: currentHum,
            led_status: ledStatus.color,
            online_clients: io.engine.clientsCount
        });
    } catch (err) {
        res.status(500).json({ error: "Server error" });
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

// Health check
app.get("/health", (req, res) => {
    res.json({
        status: "running",
        uptime: process.uptime(),
        logs: logs.length,
        attendance: attendance.length,
        led_status: ledStatus,
        current_temp: currentTemp,
        current_hum: currentHum
    });
});

// 404 handler
app.use((req, res) => {
    res.status(404).json({ error: "Not found" });
});

// Error handler
app.use((err, req, res, next) => {
    console.error("Server error:", err);
    res.status(500).json({ error: "Internal server error" });
});

// Load data
loadData();

// Auto-save
setInterval(saveData, 5 * 60 * 1000);

// Save on exit
process.on('SIGINT', () => {
    console.log("\n💾 Saving data...");
    saveData();
    process.exit(0);
});

process.on('SIGTERM', () => {
    console.log("\n💾 Saving data...");
    saveData();
    process.exit(0);
});

// Start server
const PORT = process.env.PORT || 3000;
const HOST = '0.0.0.0';

server.listen(PORT, HOST, () => {
    console.log("\n" + "=".repeat(50));
    console.log("🚀 IOT SERVER STARTED");
    console.log("=".repeat(50));
    console.log(`📡 Server: http://localhost:${PORT}`);
    
    // Show network IPs
    const os = require('os');
    const ifaces = os.networkInterfaces();
    
    Object.keys(ifaces).forEach(ifname => {
        ifaces[ifname].forEach(iface => {
            if ('IPv4' === iface.family && !iface.internal) {
                console.log(`🌐 Network: http://${iface.address}:${PORT}`);
            }
        });
    });
    
    console.log("=".repeat(50));
    console.log(`🔐 Login: admin / admin`);
    console.log(`📊 Logs: ${logs.length}, Attendance: ${attendance.length}`);
    console.log(`💡 LED: ${ledStatus.color}`);
    console.log("=".repeat(50));
});
