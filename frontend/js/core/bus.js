// Minimaler Event-Bus: verbindet Seiten und Komponenten lose miteinander.
const handlers = new Map();

export const bus = {
  on(event, fn) {
    if (!handlers.has(event)) handlers.set(event, new Set());
    handlers.get(event).add(fn);
    return () => handlers.get(event)?.delete(fn);
  },
  emit(event, payload) {
    handlers.get(event)?.forEach((fn) => {
      try { fn(payload); } catch (err) { console.error(`[bus] ${event}`, err); }
    });
  },
};
