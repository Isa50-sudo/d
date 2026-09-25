// Hash-Router: Seiten wechseln ohne Neuladen; Seiten können per Bus navigieren.
import { bus } from "../core/bus.js";

const PAGES = ["jarvis", "world", "system", "memory", "email", "logs", "settings"];
let currentPage = null;

export function currentPageName() {
  return currentPage;
}

export function navigate(page) {
  if (!PAGES.includes(page)) page = "jarvis";
  if (location.hash !== `#${page}`) {
    history.replaceState(null, "", `#${page}`);
  }
  apply(page);
}

function apply(page) {
  if (page === currentPage) return;
  const prev = currentPage;
  currentPage = page;
  document.querySelectorAll(".page").forEach((s) => s.classList.toggle("active", s.dataset.page === page));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
  document.body.classList.toggle("not-jarvis", page !== "jarvis");
  bus.emit("page:leave", prev);
  bus.emit("page:enter", page);
  bus.emit(`page:enter:${page}`);
}

export function initNav() {
  window.addEventListener("hashchange", () => apply(location.hash.slice(1) || "jarvis"));
  bus.on("ws:ui.navigate", ({ page }) => navigate(page));
  document.getElementById("mini-core").addEventListener("click", () => navigate("jarvis"));
  apply(location.hash.slice(1) || "jarvis");
}
