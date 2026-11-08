/*
 * Archie website widget. Paste on any page:
 *   <script src="https://YOUR-ARCHIE/widget.js" data-chatbot="SHARE-TOKEN" async></script>
 * A round button appears in the bottom-right corner; it opens the shared chatbot in a panel.
 * Optional: data-color="#9e2a4a" (button colour), data-label="Ask a question" (button label for screen readers).
 */
(() => {
  const script = document.currentScript;
  const token = script?.dataset.chatbot;
  if (!script || !token) return;
  const origin = new URL(script.src).origin;
  const ease = "cubic-bezier(0.23, 1, 0.32, 1)";
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", script.dataset.label || "Ask a question");
  button.setAttribute("aria-expanded", "false");
  button.innerHTML =
    '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-5A8 8 0 1 1 21 12Z"/></svg>';
  Object.assign(button.style, {
    position: "fixed",
    right: "20px",
    bottom: "20px",
    zIndex: "2147483000",
    width: "56px",
    height: "56px",
    borderRadius: "50%",
    border: "0",
    display: "grid",
    placeItems: "center",
    color: "#fff",
    background: script.dataset.color || "#9e2a4a",
    boxShadow: "0 6px 20px rgb(0 0 0 / 0.22)",
    transition: `transform 160ms ${ease}`,
  });
  button.addEventListener("pointerdown", () => {
    button.style.transform = "scale(0.95)";
  });
  for (const type of ["pointerup", "pointerleave"]) {
    button.addEventListener(type, () => {
      button.style.transform = "";
    });
  }

  let frame = null;
  let open = false;

  function show(next) {
    open = next;
    button.setAttribute("aria-expanded", String(open));
    if (!frame) {
      frame = document.createElement("iframe");
      frame.src = `${origin}/share/${encodeURIComponent(token)}`;
      frame.title = "Chat";
      Object.assign(frame.style, {
        position: "fixed",
        right: "20px",
        bottom: "88px",
        zIndex: "2147483000",
        width: "min(400px, calc(100vw - 40px))",
        height: "min(640px, calc(100vh - 120px))",
        border: "0",
        borderRadius: "16px",
        background: "#f5f5f7",
        boxShadow: "0 0 0 0.5px rgb(0 0 0 / 0.12), 0 22px 70px rgb(0 0 0 / 0.28)",
        transformOrigin: "bottom right",
        transition: still ? `opacity 150ms ${ease}` : `opacity 200ms ${ease}, transform 240ms ${ease}`,
        opacity: "0",
        transform: still ? "none" : "scale(0.95)",
      });
      document.body.appendChild(frame);
      frame.getBoundingClientRect(); // commit the starting style so the first opening animates
    }
    frame.style.pointerEvents = open ? "auto" : "none";
    frame.style.opacity = open ? "1" : "0";
    if (!still) frame.style.transform = open ? "none" : "scale(0.95)";
    frame.setAttribute("aria-hidden", String(!open));
    if (open) frame.focus();
  }

  button.addEventListener("click", () => show(!open));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) {
      show(false);
      button.focus();
    }
  });
  document.body.appendChild(button);
})();
