const socket = io();
socket.on("log", data => {
    document.getElementById("temp").innerText = data.temp;
    document.getElementById("hum").innerText = data.hum;
    const div = document.createElement("div");
    div.innerText = `[${data.type}] ${data.message}`;
    document.getElementById("logs").prepend(div);
});
function setLED(color) {
    fetch("/api/iot/led", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ color })
    });
}
