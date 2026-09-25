// Benachrichtigungen (Toasts) + optionale Browser-Benachrichtigungen.
export function toast(title, message, level = "info", ms = 6000) {
  const box = document.getElementById("toasts");
  const node = document.createElement("div");
  node.className = `toast ${level}`;
  const b = document.createElement("b");
  b.textContent = title;
  const p = document.createElement("div");
  p.textContent = message;
  node.append(b, p);
  box.prepend(node);
  while (box.children.length > 5) box.lastChild.remove();
  setTimeout(() => {
    node.classList.add("out");
    setTimeout(() => node.remove(), 320);
  }, ms);
}

export function browserNotify(title, message) {
  if (!("Notification" in window) || !document.hidden) return;
  if (Notification.permission === "granted") {
    new Notification(`JARVIS · ${title}`, { body: message, icon: "assets/favicon.svg", silent: false });
  }
}

export async function requestBrowserNotifications() {
  if ("Notification" in window && Notification.permission === "default") {
    await Notification.requestPermission();
  }
}
