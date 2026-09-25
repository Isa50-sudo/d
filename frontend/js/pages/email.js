// EMAIL: Posteingang (IMAP), Lesen, Entwürfe, Senden (immer mit Bestätigung).
import { api } from "../core/api.js";
import { bus } from "../core/bus.js";
import { escapeHtml } from "../core/store.js";
import { toast } from "../components/toasts.js";

const $ = (id) => document.getElementById(id);
let ready = false;
let searchTimer = null;

async function status() {
  const st = await api.get("/api/email/status");
  ready = st.configured && st.password_stored;
  const setup = $("email-setup");
  $("email-main").classList.toggle("hidden", !ready);
  setup.classList.toggle("hidden", ready);
  if (!ready) {
    setup.innerHTML = `
      <div class="panel-head"><span>E-MAIL-MODUL EINRICHTEN</span></div>
      <p>${escapeHtml(st.message)}</p>
      <ol class="small" style="line-height:1.9">
        <li>In <code>.env</code> eintragen: <code>EMAIL_ADDRESS</code>, <code>EMAIL_IMAP_HOST</code> (z. B. imap.gmail.com), <code>EMAIL_SMTP_HOST</code> (z. B. smtp.gmail.com), Ports.</li>
        <li>Passwort sicher im Schlüsselbund des Betriebssystems speichern:<br /><code>python scripts/set_email_password.py</code><br /><span class="dim">Bei Gmail/Outlook mit 2FA ein App-Passwort verwenden. Das Passwort wird nie im Klartext gespeichert.</span></li>
        <li>JARVIS neu starten.</li>
      </ol>`;
  }
  return ready;
}

async function loadList() {
  if (!ready) return;
  const list = $("mail-list");
  list.innerHTML = `<li class="dim">Lade …</li>`;
  const q = $("mail-search").value.trim();
  const unread = $("mail-unread").checked;
  try {
    const data = await api.get(`/api/email/messages?limit=30&unread_only=${unread}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
    list.innerHTML = data.messages.length
      ? data.messages
          .map((m) => `<li data-uid="${m.uid}" class="${m.unread ? "unread" : ""}">
            <span class="from"><span>${escapeHtml(m.from_name || m.from_address)}</span>${m.likely_important ? '<span class="imp">WICHTIG</span>' : ""}</span>
            <span class="subj">${escapeHtml(m.subject || "(kein Betreff)")}</span>
            <span class="dim small">${m.date ? escapeHtml(new Date(m.date).toLocaleString("de-DE")) : ""}</span></li>`)
          .join("")
      : `<li class="dim">Keine E-Mails gefunden.</li>`;
  } catch (err) {
    list.innerHTML = `<li class="dim">${escapeHtml(err.message)}</li>`;
  }
}

async function openMail(uid, li) {
  document.querySelectorAll("#mail-list li").forEach((n) => n.classList.toggle("sel", n === li));
  const view = $("mail-view");
  view.textContent = "Lade …";
  try {
    const m = await api.get(`/api/email/messages/${uid}`);
    view.classList.remove("dim");
    view.innerHTML = `<h3>${escapeHtml(m.subject || "(kein Betreff)")}</h3>
      <div class="dim small">Von: ${escapeHtml(m.from_name)} &lt;${escapeHtml(m.from_address)}&gt; · ${m.date ? escapeHtml(new Date(m.date).toLocaleString("de-DE")) : ""}<br />An: ${escapeHtml(m.to)}${m.attachments?.length ? `<br />Anhänge: ${escapeHtml(m.attachments.join(", "))}` : ""}</div><hr style="border-color:var(--line)" /><div></div>`;
    view.lastElementChild.textContent = m.body;
    $("mc-to").value = m.from_address;
    $("mc-subject").value = m.subject?.startsWith("Re:") ? m.subject : `Re: ${m.subject || ""}`;
  } catch (err) {
    view.textContent = err.message;
  }
}

function compose() {
  return { to: $("mc-to").value.trim(), subject: $("mc-subject").value.trim(), body: $("mc-body").value };
}

export function initEmailPage() {
  bus.on("page:enter:email", async () => {
    try { if (await status()) loadList(); } catch (err) { toast("E-Mail", err.message, "error"); }
  });
  $("mail-refresh").addEventListener("click", loadList);
  $("mail-unread").addEventListener("change", loadList);
  $("mail-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadList, 400);
  });
  $("mail-list").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-uid]");
    if (li) openMail(li.dataset.uid, li);
  });
  $("mc-draft").addEventListener("click", async () => {
    try {
      const r = await api.post("/api/email/draft", compose());
      toast("E-Mail", `Entwurf gespeichert (${r.draft_saved_in}).`);
    } catch (err) { toast("E-Mail", err.message, "error"); }
  });
  $("mail-compose").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/api/email/send", compose()); // Backend fordert Bestätigung an
      toast("E-Mail", "E-Mail wurde gesendet.");
      $("mc-body").value = "";
    } catch (err) { toast("E-Mail", err.message, "warning"); }
  });
  bus.on("ws:email.new", () => document.querySelector(".page.active[data-page='email']") && loadList());
}
