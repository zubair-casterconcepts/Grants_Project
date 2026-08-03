(() => {
  const loader = document.getElementById("page-loader");
  if (!loader) return;

  const messageEl = loader.querySelector("[data-loader-message]");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let hideTimer = null;
  let messageTimer = null;
  let active = false;

  const messagesByPath = [
    { test: /\/home\/matches/i, text: "Loading…" },
    { test: /\/home\/?$/i, text: "Opening dashboard…" },
    { test: /\/accounts\/saved\/add/i, text: "Saving grant…" },
    { test: /\/accounts\/saved\/.+\/remove/i, text: "Removing grant…" },
    { test: /\/accounts\/saved/i, text: "Opening saved grants…" },
    { test: /\/accounts\/settings/i, text: "Opening settings…" },
    { test: /\/accounts\/onboarding/i, text: "Preparing your profile…" },
    { test: /\/login/i, text: "Signing you in…" },
    { test: /\/logout/i, text: "Signing you out…" },
  ];

  const rotatingHints = [];

  function resolveMessage(url, explicit) {
    if (explicit) return explicit;
    try {
      const path = new URL(url, window.location.origin).pathname;
      const hit = messagesByPath.find((row) => row.test.test(path));
      if (hit) return hit.text;
    } catch (_) {
      /* ignore bad urls */
    }
    return "Loading…";
  }

  function setMessage(text) {
    if (!messageEl) return;
    messageEl.textContent = text;
  }

  function swapMessage(text) {
    if (!messageEl || reducedMotion) {
      setMessage(text);
      return;
    }
    messageEl.classList.add("is-swap");
    window.setTimeout(() => {
      setMessage(text);
      messageEl.classList.remove("is-swap");
    }, 160);
  }

  function showLoader(message) {
    if (hideTimer) {
      window.clearTimeout(hideTimer);
      hideTimer = null;
    }
    setMessage(message || "Loading…");
    loader.hidden = false;
    loader.setAttribute("aria-hidden", "false");
    // Force reflow so the transition runs.
    void loader.offsetWidth;
    loader.classList.add("is-active");
    active = true;
    document.documentElement.classList.add("is-page-loading");
  }

  function hideLoader() {
    if (!active && !loader.classList.contains("is-active")) {
      loader.hidden = true;
      return;
    }
    loader.classList.remove("is-active");
    loader.setAttribute("aria-hidden", "true");
    document.documentElement.classList.remove("is-page-loading");
    active = false;
    if (messageTimer) {
      window.clearInterval(messageTimer);
      messageTimer = null;
    }
    hideTimer = window.setTimeout(() => {
      loader.hidden = true;
    }, 300);
  }

  function startRotatingHints(_url) {
    if (messageTimer) {
      window.clearInterval(messageTimer);
      messageTimer = null;
    }
    // Full-page loader stays brief; grant ranking uses the dashboard section loader.
  }

  function shouldIgnoreLink(anchor) {
    if (!anchor || !anchor.href) return true;
    if (anchor.target === "_blank") return true;
    if (anchor.hasAttribute("download")) return true;
    if (anchor.dataset.noLoader === "true") return true;

    const href = anchor.getAttribute("href") || "";
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) return true;

    try {
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return true;
      if (
        url.pathname === window.location.pathname &&
        url.search === window.location.search &&
        url.hash
      ) {
        return true;
      }
    } catch (_) {
      return true;
    }
    return false;
  }

  document.addEventListener(
    "click",
    (event) => {
      const anchor = event.target.closest("a[href]");
      if (!anchor || shouldIgnoreLink(anchor)) return;
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const message = resolveMessage(anchor.href, anchor.dataset.loaderMessage);
      showLoader(message);
      startRotatingHints(anchor.href);
    },
    true
  );

  document.addEventListener(
    "submit",
    (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.dataset.noLoader === "true") return;
      // Chat / AJAX forms must never flash the full-page loader.
      if (form.id === "chat-composer" || form.classList.contains("chat-composer")) {
        return;
      }
      if (event.defaultPrevented) return;

      const action = form.getAttribute("action") || window.location.href;
      // Same-page chat posts (empty action on /home/) are not real navigations.
      try {
        const url = new URL(action, window.location.href);
        if (
          url.pathname === window.location.pathname &&
          !form.getAttribute("action")
        ) {
          return;
        }
      } catch (_) {
        /* continue */
      }

      const message = resolveMessage(action, form.dataset.loaderMessage);
      showLoader(message);
      startRotatingHints(action);
    },
    true
  );

  window.addEventListener("pageshow", () => {
    hideLoader();
  });

  document.addEventListener("DOMContentLoaded", () => {
    hideLoader();
  });

  // Expose for rare manual use (e.g. future async sections).
  window.GrantsPageLoader = { show: showLoader, hide: hideLoader };
})();
