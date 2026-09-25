// WebSocket-Verbindung zum lokalen Backend mit automatischer Wiederverbindung.
// JSON-Nachrichten -> bus "ws:<type>", Binärdaten (JARVIS-Stimme) -> bus "audio:chunk".
import { bus } from "./bus.js";
import { setStore } from "./store.js";

let socket = null;
let retry = 0;
let pingTimer = null;

export function connect() {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  socket = new WebSocket(url);
  socket.binaryType = "arraybuffer";

  socket.onopen = () => {
    retry = 0;
    setStore({ backend: "online" });
    bus.emit("ws:open");
    clearInterval(pingTimer);
    pingTimer = setInterval(() => sendJson({ type: "ping" }), 20000);
  };

  socket.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      bus.emit("audio:chunk", event.data);
      return;
    }
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    bus.emit(`ws:${msg.type}`, msg);
    bus.emit("ws:any", msg);
  };

  socket.onclose = () => {
    clearInterval(pingTimer);
    setStore({ backend: "offline" });
    bus.emit("ws:close");
    const delay = Math.min(10000, 500 * 2 ** retry++);
    setTimeout(connect, delay);
  };

  socket.onerror = () => socket.close();
}

export function sendJson(payload) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

export function sendBinary(buffer) {
  // Rückstau vermeiden: bei überlasteter Verbindung Audio verwerfen statt Latenz aufbauen
  if (socket?.readyState === WebSocket.OPEN && socket.bufferedAmount < 256 * 1024) {
    socket.send(buffer);
  }
}

export const isOpen = () => socket?.readyState === WebSocket.OPEN;
