(() => {
  const app = document.getElementById("chat-app");
  const transcript = document.getElementById("chat-transcript");
  const form = document.getElementById("chat-composer");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const suggestionsEl = document.getElementById("chat-suggestions");
  const bootstrapEl = document.getElementById("chat-bootstrap");
  const starterEl = document.getElementById("chat-starter");
  const starterTitleEl = document.getElementById("chat-starter-title");
  const starterCopyEl = document.getElementById("chat-starter-copy");
  const starterGridEl = document.getElementById("chat-starter-grid");
  if (!app || !transcript || !form || !input || !bootstrapEl) return;

  const matchesUrl = app.dataset.matchesUrl || "/home/matches/";
  const matchesStreamUrl =
    app.dataset.matchesStreamUrl || "/home/matches/stream/";
  const profileUrl = app.dataset.profileUrl || "/home/chat/profile/";
  const conversationsUrl =
    app.dataset.conversationsUrl || "/home/conversations/";
  const saveUrl = app.dataset.saveUrl || "/accounts/saved/add/";
  const homeUrl = app.dataset.homeUrl || "/home/";
  const csrfToken =
    app.dataset.csrfToken ||
    (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] ||
    "";

  const conversationsBootstrapEl = document.getElementById(
    "conversations-bootstrap"
  );
  const listEl = document.getElementById("chat-conversation-list");
  const titleEl = document.getElementById("chat-active-title");
  const newChatBtn = document.getElementById("chat-new-btn");
  const sidebar = document.getElementById("chat-sidebar");
  const sidebarToggle = document.getElementById("chat-sidebar-toggle");
  const sidebarClose = document.getElementById("chat-sidebar-close");
  const sidebarBackdrop = document.getElementById("chat-sidebar-backdrop");
  const SIDEBAR_STORAGE_KEY = "grants.chat.sidebarCollapsed";

  let bootstrap = JSON.parse(bootstrapEl.textContent || "{}");
  let conversationsState = conversationsBootstrapEl
    ? JSON.parse(conversationsBootstrapEl.textContent || "{}")
    : { active_id: null, conversations: [] };
  let conversationId = asConversationId(
    app.dataset.activeConversationId || conversationsState.active_id
  );
  let conversations = Array.isArray(conversationsState.conversations)
    ? conversationsState.conversations
    : [];
  let busy = false;
  let keepBusy = false;
  let currentStep = null;
  let persistEnabled = true;
  const LOAD_CONVERSATION_ERROR =
    "I couldn't load that conversation. Try another one or start a new chat.";

  function asConversationId(value) {
    const id = Number(value);
    return Number.isFinite(id) && id > 0 ? id : null;
  }

  function isSameConversation(a, b) {
    const left = asConversationId(a);
    const right = asConversationId(b);
    return Boolean(left && right && left === right);
  }

  const INTAKE_STEPS = [
    {
      id: "organization",
      prompt: "What's the name of your organization?",
      placeholder: "e.g. Riverside Community Center",
    },
    {
      id: "role_title",
      prompt: "What's your role there? (optional — you can say skip)",
      placeholder: "e.g. Grant Writer",
      optional: true,
    },
    {
      id: "title",
      prompt: "What are you seeking funding for? Give it a short title.",
      placeholder: "e.g. Youth after-school STEM program",
    },
    {
      id: "description",
      prompt: "Tell me a bit about the work — what it does and who it serves.",
      placeholder: "Describe the project…",
    },
    {
      id: "priority_area",
      prompt: "Which priority area fits best? Pick one below or type it.",
      choiceKey: "priority_area",
    },
    {
      id: "location_city",
      prompt: "What city is this based in?",
      placeholder: "e.g. Albany",
    },
    {
      id: "location_state",
      prompt: "And which state? Pick one or type the 2-letter code.",
      choiceKey: "location_state",
      placeholder: "e.g. NY",
    },
    {
      id: "org_type",
      prompt: "What type of organization is this?",
      choiceKey: "org_type",
    },
    {
      id: "budget_requested",
      prompt: "About how much funding are you looking for? (USD)",
      placeholder: "e.g. 50000",
    },
    {
      id: "eligibility_notes",
      prompt: "Any eligibility notes I should know? (optional — say skip)",
      placeholder: "Optional notes…",
      optional: true,
    },
  ];

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function attr(value) {
    return escapeHtml(value).replace(/\n/g, " ");
  }

  // Keep the viewport pinned to newest streamed content unless the user scrolls up.
  const SCROLL_PIN_PX = 140;
  let stickToBottom = true;
  let scrollRaf = 0;
  let ignoreScrollEvent = false;

  function distanceFromBottom() {
    const doc = document.documentElement;
    const body = document.body;
    const scrollTop = window.scrollY || doc.scrollTop || body.scrollTop || 0;
    const viewport = window.innerHeight || doc.clientHeight || 0;
    const height = Math.max(doc.scrollHeight || 0, body.scrollHeight || 0);
    return height - (scrollTop + viewport);
  }

  function updateStickToBottom() {
    if (ignoreScrollEvent) return;
    stickToBottom = distanceFromBottom() <= SCROLL_PIN_PX;
  }

  window.addEventListener("scroll", updateStickToBottom, { passive: true });

  function scrollToBottom(options = {}) {
    // Full-page scroll (ChatGPT-style) — no inner transcript scrollbar.
    // Instant + rAF-coalesced during streaming; smooth only when explicitly requested.
    const force = options.force === true;
    const smooth = options.smooth === true;
    if (!force && !stickToBottom) return;

    const run = () => {
      scrollRaf = 0;
      // Re-check: the user may have scrolled up after this frame was queued.
      if (!force && !stickToBottom) return;
      const top = Math.max(
        document.documentElement.scrollHeight || 0,
        document.body.scrollHeight || 0
      );
      ignoreScrollEvent = true;
      window.scrollTo({
        top,
        left: 0,
        behavior: smooth ? "smooth" : "auto",
      });
      stickToBottom = true;
      // Allow the browser to apply layout before re-enabling scroll tracking.
      requestAnimationFrame(() => {
        ignoreScrollEvent = false;
        updateStickToBottom();
      });
    };

    if (smooth) {
      if (scrollRaf) {
        cancelAnimationFrame(scrollRaf);
        scrollRaf = 0;
      }
      run();
      return;
    }

    if (scrollRaf) cancelAnimationFrame(scrollRaf);
    scrollRaf = requestAnimationFrame(run);
  }

  // Streaming rewrites content above the viewport (status text rewraps, the
  // status row is dropped when results land). Anchoring on the topmost visible
  // row keeps the reader on the same content instead of shifting under them.
  function captureScrollAnchor() {
    if (stickToBottom || !transcript) return null;
    const anchors = [];
    for (const row of Array.from(transcript.children)) {
      const rect = row.getBoundingClientRect();
      if (rect.bottom <= 0) continue;
      anchors.push({ row, top: rect.top });
      if (anchors.length >= 3) break;
    }
    return anchors.length ? anchors : null;
  }

  function restoreScrollAnchor(anchors) {
    if (!anchors) return;
    const anchor = anchors.find((item) => item.row.isConnected);
    if (!anchor) return;
    const delta = anchor.row.getBoundingClientRect().top - anchor.top;
    if (Math.abs(delta) < 1) return;
    ignoreScrollEvent = true;
    window.scrollBy({ top: delta, left: 0, behavior: "auto" });
    requestAnimationFrame(() => {
      ignoreScrollEvent = false;
      updateStickToBottom();
    });
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  }

  function appendRow(role, html) {
    const row = document.createElement("div");
    row.className = `chat-row is-${role}`;
    row.innerHTML = html;
    transcript.appendChild(row);
    // User messages always re-pin; assistant/stream updates follow stick-to-bottom.
    scrollToBottom({ force: role === "user" });
    return row;
  }

  async function appendText(role, text, options = {}) {
    const row = appendRow(
      role,
      `<div class="chat-bubble">${escapeHtml(text)}</div>`
    );
    if (options.persist !== false) {
      await persistMessage(role, text, options.metadata || {});
    }
    return row;
  }

  async function appendAssistantHtml(inner, options = {}) {
    const row = appendRow("assistant", `<div class="chat-bubble">${inner}</div>`);
    if (options.persist !== false && options.persistContent) {
      await persistMessage("assistant", options.persistContent, options.metadata || {});
    }
    return row;
  }

  async function ensureConversation() {
    if (conversationId) return conversationId;
    // Always start as "New chat"; first user message gets an AI short title.
    const response = await fetch(conversationsUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-CSRFToken": csrfToken,
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify({ title: "New chat" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok || !data.conversation) {
      throw new Error("create_failed");
    }
    conversationId = asConversationId(data.conversation.id);
    if (!conversationId) throw new Error("create_failed");
    upsertConversationLocal(data.conversation);
    renderConversationList();
    setActiveTitle(data.conversation.title);
    return conversationId;
  }

  async function persistMessage(role, content, metadata = {}) {
    if (!persistEnabled) return null;
    const text = String(content || "").trim();
    if (!text && !(metadata && Object.keys(metadata).length)) return null;

    // Create a DB thread only when the user sends the first message.
    if (!conversationId) {
      if (role !== "user") return null;
      try {
        await ensureConversation();
      } catch (_) {
        return null;
      }
    }
    if (!conversationId) return null;

    try {
      const response = await fetch(
        `${conversationsUrl}${conversationId}/messages/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
            "X-CSRFToken": csrfToken,
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
          body: JSON.stringify({ role, content: text, metadata }),
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) return null;
      if (data.conversation) {
        upsertConversationLocal(data.conversation);
        renderConversationList();
        setActiveTitle(data.conversation.title);
      }
      return data.message;
    } catch (_) {
      return null;
    }
  }

  async function syncProjectSnapshot() {
    if (!conversationId) return;
    try {
      const response = await fetch(
        `${conversationsUrl}${conversationId}/sync-project/`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-CSRFToken": csrfToken,
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "same-origin",
          body: "{}",
        }
      );
      const data = await response.json().catch(() => ({}));
      if (data.bootstrap) bootstrap = data.bootstrap;
      if (data.conversation) {
        upsertConversationLocal(data.conversation);
        renderConversationList();
        setActiveTitle(data.conversation.title);
      }
    } catch (_) {
      /* non-fatal */
    }
  }

  function upsertConversationLocal(conversation) {
    if (!conversation || !conversation.id) return;
    const id = asConversationId(conversation.id);
    if (!id) return;
    const normalized = { ...conversation, id };
    const rest = conversations.filter((row) => !isSameConversation(row.id, id));
    conversations = [normalized, ...rest];
  }

  function setActiveTitle(title) {
    if (titleEl) titleEl.textContent = title || "New chat";
  }

  function closeAllConversationMenus() {
    if (!listEl) return;
    listEl.querySelectorAll(".chat-conversation-row.is-menu-open").forEach((row) => {
      row.classList.remove("is-menu-open");
    });
    listEl.querySelectorAll(".chat-conversation-dropdown.is-open").forEach((menu) => {
      menu.classList.remove("is-open");
    });
    listEl.querySelectorAll(".chat-conversation-more[aria-expanded='true']").forEach((btn) => {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  const deleteModal = document.getElementById("chat-delete-modal");
  const deleteBackdrop = document.getElementById("chat-delete-backdrop");
  const deleteCancelBtn = document.getElementById("chat-delete-cancel");
  const deleteConfirmBtn = document.getElementById("chat-delete-confirm");
  const deleteNameEl = document.getElementById("chat-delete-name");
  let deleteResolver = null;
  let deleteFocusReturn = null;

  function closeDeleteModal(result) {
    if (!deleteModal || deleteModal.hidden) {
      if (deleteResolver) {
        const resolve = deleteResolver;
        deleteResolver = null;
        resolve(Boolean(result));
      }
      return;
    }
    deleteModal.hidden = true;
    document.body.classList.remove("chat-modal-open");
    const resolve = deleteResolver;
    deleteResolver = null;
    if (deleteFocusReturn && typeof deleteFocusReturn.focus === "function") {
      deleteFocusReturn.focus();
    }
    deleteFocusReturn = null;
    if (resolve) resolve(Boolean(result));
  }

  function openDeleteModal(title) {
    return new Promise((resolve) => {
      if (!deleteModal || !deleteConfirmBtn) {
        resolve(false);
        return;
      }
      if (deleteResolver) closeDeleteModal(false);
      deleteResolver = resolve;
      deleteFocusReturn = document.activeElement;
      if (deleteNameEl) {
        deleteNameEl.textContent = title || "this chat";
      }
      deleteModal.hidden = false;
      document.body.classList.add("chat-modal-open");
      deleteConfirmBtn.focus();
    });
  }

  if (deleteCancelBtn) {
    deleteCancelBtn.addEventListener("click", () => closeDeleteModal(false));
  }
  if (deleteBackdrop) {
    deleteBackdrop.addEventListener("click", () => closeDeleteModal(false));
  }
  if (deleteConfirmBtn) {
    deleteConfirmBtn.addEventListener("click", () => closeDeleteModal(true));
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && deleteModal && !deleteModal.hidden) {
      event.preventDefault();
      closeDeleteModal(false);
    }
  });

  async function deleteConversation(id, title) {
    if (!id || busy) return;
    closeAllConversationMenus();
    const confirmed = await openDeleteModal(title || "this chat");
    if (!confirmed) return;

    busy = true;
    setComposerEnabled(false);
    if (deleteConfirmBtn) deleteConfirmBtn.disabled = true;
    try {
      const response = await fetch(`${conversationsUrl}${id}/`, {
        method: "DELETE",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error("delete_failed");

      conversations = Array.isArray(data.conversations) ? data.conversations : [];
      const wasActive = isSameConversation(id, conversationId);
      renderConversationList();

      if (!wasActive) return;

      if (data.next_id) {
        await loadConversation(data.next_id, { force: true });
      } else {
        conversationId = null;
        createNewChat();
      }
    } catch (_) {
      appendText(
        "assistant",
        "I couldn't delete that chat. Please try again.",
        { persist: false }
      );
    } finally {
      busy = false;
      setComposerEnabled(true);
      if (deleteConfirmBtn) deleteConfirmBtn.disabled = false;
    }
  }

  function renderConversationList() {
    if (!listEl) return;
    listEl.innerHTML = "";
    if (!conversations.length) {
      listEl.innerHTML = `<p class="chat-conversation-empty">No chats yet</p>`;
      return;
    }
    conversations.forEach((row) => {
      const rowId = asConversationId(row.id);
      if (!rowId) return;
      const wrap = document.createElement("div");
      wrap.className = `chat-conversation-row${
        isSameConversation(rowId, conversationId) ? " is-active" : ""
      }`;
      wrap.dataset.conversationId = String(rowId);

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-conversation-item";
      btn.textContent = row.title || "New chat";
      btn.title = row.title || "New chat";
      btn.addEventListener("click", () => {
        // Same thread (number/string id safe) or busy matching — do not reload.
        if (isSameConversation(rowId, conversationId) || busy || keepBusy) return;
        closeAllConversationMenus();
        loadConversation(rowId);
        closeSidebarMobile();
      });

      const menuWrap = document.createElement("div");
      menuWrap.className = "chat-conversation-menu";

      const moreBtn = document.createElement("button");
      moreBtn.type = "button";
      moreBtn.className = "chat-conversation-more";
      moreBtn.setAttribute("aria-label", `Chat options for ${row.title || "New chat"}`);
      moreBtn.setAttribute("aria-haspopup", "menu");
      moreBtn.setAttribute("aria-expanded", "false");
      moreBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <circle cx="12" cy="5" r="1.7"/>
          <circle cx="12" cy="12" r="1.7"/>
          <circle cx="12" cy="19" r="1.7"/>
        </svg>
      `;

      const dropdown = document.createElement("div");
      dropdown.className = "chat-conversation-dropdown";
      dropdown.setAttribute("role", "menu");

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "chat-conversation-delete";
      deleteBtn.setAttribute("role", "menuitem");
      deleteBtn.textContent = "Delete";
      deleteBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        deleteConversation(row.id, row.title || "New chat");
      });

      moreBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const willOpen = !dropdown.classList.contains("is-open");
        closeAllConversationMenus();
        if (willOpen) {
          dropdown.classList.add("is-open");
          wrap.classList.add("is-menu-open");
          moreBtn.setAttribute("aria-expanded", "true");
        }
      });

      dropdown.appendChild(deleteBtn);
      menuWrap.appendChild(moreBtn);
      menuWrap.appendChild(dropdown);
      wrap.appendChild(btn);
      wrap.appendChild(menuWrap);
      listEl.appendChild(wrap);
    });
  }

  function isMobileSidebar() {
    return window.matchMedia("(max-width: 900px)").matches;
  }

  function setSidebarCollapsed(collapsed) {
    document.body.classList.toggle("chat-sidebar-collapsed", !!collapsed);
    try {
      localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
    } catch (_) {
      /* ignore */
    }
    if (sidebarToggle) {
      sidebarToggle.setAttribute(
        "aria-label",
        collapsed || isMobileSidebar() ? "Open sidebar" : "Open sidebar"
      );
      sidebarToggle.title = "Open sidebar";
    }
  }

  function openSidebar() {
    if (isMobileSidebar()) {
      document.body.classList.add("chat-sidebar-open");
      if (sidebarBackdrop) sidebarBackdrop.hidden = false;
      return;
    }
    setSidebarCollapsed(false);
  }

  function closeSidebar() {
    if (isMobileSidebar()) {
      document.body.classList.remove("chat-sidebar-open");
      if (sidebarBackdrop) sidebarBackdrop.hidden = true;
      return;
    }
    setSidebarCollapsed(true);
  }

  function openSidebarMobile() {
    if (!isMobileSidebar()) return;
    document.body.classList.add("chat-sidebar-open");
    if (sidebarBackdrop) sidebarBackdrop.hidden = false;
  }

  function closeSidebarMobile() {
    // Only dismiss the mobile drawer — never collapse the desktop sidebar.
    if (!isMobileSidebar()) return;
    document.body.classList.remove("chat-sidebar-open");
    if (sidebarBackdrop) sidebarBackdrop.hidden = true;
  }

  function clearTranscript() {
    transcript.innerHTML = "";
    clearSuggestions();
    currentStep = null;
    hideStarter();
  }

  function hideStarter() {
    if (starterEl) starterEl.hidden = true;
    app.classList.remove("is-starter");
  }

  // Fallback used only if the server didn't send starter_prompts (e.g. an old
  // cached page). Mirrors _DEFAULT_STARTER_PROMPTS in the backend.
  const DEFAULT_STARTER_PROMPTS = [
    { title: "Find grants", description: "Search by focus & location", action: "search", query: "find grants", href: "" },
    { title: "Update my project", description: "Refresh intake details", action: "update_project", query: "", href: "" },
    { title: "View saved", description: "Open grants you saved", action: "link", query: "", href: "/accounts/saved/" },
    { title: "Ask anything", description: "Type a question below", action: "focus_input", query: "", href: "" },
  ];

  // The onboarded starter cards come from the DB (bootstrap.starter_prompts).
  function starterCardsForReady() {
    const prompts = Array.isArray(bootstrap.starter_prompts)
      ? bootstrap.starter_prompts
      : [];
    const source = prompts.length ? prompts : DEFAULT_STARTER_PROMPTS;
    return source.map((p) => ({
      title: p.title || "",
      description: p.description || "",
      action: p.action || "search",
      query: p.query || "",
      href: p.href || "",
    }));
  }

  function showStarter() {
    if (!starterEl || !starterGridEl) return;
    const onboarded = !!bootstrap.onboarding_completed;
    const projectTitle =
      (bootstrap.profile && bootstrap.profile.title) || "your project";

    if (starterTitleEl) {
      starterTitleEl.textContent = onboarded
        ? "What are you looking for?"
        : "Let’s set up your project";
    }
    if (starterCopyEl) {
      starterCopyEl.textContent = onboarded
        ? `Tell me about ${projectTitle} and I'll find matching opportunities, help you refine details, and save the best fits.`
        : "Answer a few quick questions about your organization and project, then I'll search Grants.gov, USASpending, and GrantedAI.";
    }

    const cards = onboarded
      ? starterCardsForReady()
      : [
          {
            title: "Start project setup",
            description: "Answer a few quick questions",
            action: "setup",
          },
          {
            title: "What you’ll get",
            description: "Ranked matches from 3 sources",
            action: "setup",
          },
          {
            title: "View saved",
            description: "Open grants you saved",
            action: "link",
            href: "/accounts/saved/",
          },
          {
            title: "Ask anything",
            description: "Type a question below",
            action: "focus_input",
          },
        ];

    starterGridEl.innerHTML = "";
    cards.forEach((card) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-starter-card";
      // Text-only, modern card — prompts are customizable, so no per-card icon.
      btn.innerHTML = `
        <span class="chat-starter-card-copy">
          <strong>${escapeHtml(card.title)}</strong>
          <span>${escapeHtml(card.description)}</span>
        </span>
      `;
      btn.addEventListener("click", () => {
        if (busy) return;
        const action = card.action || "search";

        // Open a link (e.g. View saved).
        if (action === "link" || card.href) {
          if (card.href) window.location.href = card.href;
          return;
        }

        // Focus the composer (Ask anything).
        if (action === "focus_input") {
          hideStarter();
          setSuggestions([
            { label: "Find grants", value: "find grants" },
            { label: "Update my project", value: "update my project" },
          ]);
          input.focus();
          return;
        }

        hideStarter();

        // Launch the onboarding intake (setup cards for a new user).
        if (action === "setup") {
          const firstEmpty = INTAKE_STEPS.find((s) => {
            const value = (bootstrap.profile || {})[s.id];
            return value == null || String(value).trim() === "";
          });
          appendText(
            "assistant",
            `Hi ${bootstrap.username || "there"} — I'll ask a few quick questions about your project, then find matching opportunities.`
          );
          askStep(firstEmpty || INTAKE_STEPS[0]);
          return;
        }

        // Re-open project intake (Update my project).
        if (action === "update_project") {
          startProjectUpdate(card.title);
          return;
        }

        // Search: hand the configured query to the matcher (saved profile +
        // overrides, exactly as before). The card title is what shows in chat.
        runStarterSearch(card);
      });
      starterGridEl.appendChild(btn);
    });

    starterEl.hidden = false;
    app.classList.add("is-starter");
    clearSuggestions();
  }

  function replayMessage(message) {
    const role = message.role === "user" ? "user" : "assistant";
    const content = String(message.content || "");
    // Never replay transient system load errors into the thread.
    if (content.includes("I couldn't load that conversation")) return;
    const meta = message.metadata || {};
    if (role === "assistant" && Array.isArray(meta.matches) && meta.matches.length) {
      const summary =
        message.content ||
        `Here are ${meta.matches.length} ranked opportunities.`;
      const row = appendAssistantHtml(
        `<p class="chat-match-summary">${escapeHtml(summary)}</p>
         <div class="chat-matches">${meta.matches
           .map((m, i) => renderCard(m, i))
           .join("")}</div>
         ${matchToolbarHtml()}`,
        { persist: false }
      );
      // appendAssistantHtml is async in signature but called without await here historically —
      // it returns a Promise; normalize to element when available.
      Promise.resolve(row).then((el) => {
        if (!el) return;
        bindSaveForms(el);
        bindMatchToolbar(el);
        showMatchToolbar(el);
      });
      return;
    }
    appendText(role, message.content || "", { persist: false });
  }

  async function loadConversation(id, options = {}) {
    const targetId = asConversationId(id);
    if (!targetId) return;
    // Already viewing this thread — reloading would wipe in-progress matches.
    if (
      !options.force &&
      isSameConversation(targetId, conversationId) &&
      transcript.childElementCount > 0
    ) {
      return;
    }
    if (!options.force && (busy || keepBusy)) return;

    busy = true;
    setComposerEnabled(false);
    clearTranscript();
    try {
      const response = await fetch(`${conversationsUrl}${targetId}/`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error("load_failed");

      conversationId = asConversationId(data.conversation.id) || targetId;
      upsertConversationLocal(data.conversation);
      if (data.bootstrap) bootstrap = data.bootstrap;
      setActiveTitle(data.conversation.title);
      renderConversationList();

      const messages = Array.isArray(data.messages) ? data.messages : [];
      if (!messages.length) {
        persistEnabled = true;
        startFreshGreeting({ persist: false });
      } else {
        persistEnabled = false;
        messages.forEach(replayMessage);
        if (bootstrap.onboarding_completed) {
          setSuggestions([
            { label: "Find grants", value: "find grants" },
            { label: "Update my project", value: "update my project" },
          ]);
        } else {
          const step = nextMissingStep() || INTAKE_STEPS[0];
          const firstEmpty = INTAKE_STEPS.find((s) => {
            const value = (bootstrap.profile || {})[s.id];
            return value == null || String(value).trim() === "";
          });
          askStep(firstEmpty || step, { persist: false });
        }
        persistEnabled = true;
      }
    } catch (_) {
      persistEnabled = true;
      // Never inject this into an active/matching thread — it breaks UX.
      if (!keepBusy && transcript.childElementCount === 0) {
        await appendText("assistant", LOAD_CONVERSATION_ERROR, { persist: false });
      }
    } finally {
      busy = false;
      setComposerEnabled(true);
      input.focus();
    }
  }

  function createNewChat() {
    // Draft only — no DB thread until the user sends a message.
    if (busy) return;
    closeAllConversationMenus();
    conversationId = null;
    currentStep = null;
    clearTranscript();
    setActiveTitle("New chat");
    renderConversationList();
    startFreshGreeting({ persist: false });
    closeSidebarMobile();
    setComposerEnabled(true);
    input.focus();
  }

  function startFreshGreeting(_options = {}) {
    // Empty / new chat uses the centered predefined-instruction layout.
    showStarter();
    input.placeholder = "Message Grants…";
  }

  function showTyping() {
    return appendRow(
      "assistant",
      `<div class="chat-bubble"><div class="chat-typing" aria-label="Assistant is typing"><span></span><span></span><span></span></div></div>`
    );
  }

  const THINKING_CLOCK_ICON = `
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8.25" stroke="currentColor" stroke-width="1.6"/>
      <path d="M12 8.2V12l2.6 1.7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  `;

  const THINKING_DONE_ICON = `
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="8.25" stroke="currentColor" stroke-width="1.6"/>
      <path d="m8.7 12.2 2.2 2.2 4.4-4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  `;

  function renderThinkingHtml(linesText, options = {}) {
    const lines = String(linesText || "")
      .split(/\n+/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map(
        (line) =>
          `<p class="chat-thinking-line">${escapeHtml(line)}</p>`
      )
      .join("");
    const done = options.done
      ? `<div class="chat-thinking-done"><span class="chat-thinking-done-icon">${THINKING_DONE_ICON}</span><span>Done</span></div>`
      : "";
    return `
      <div class="chat-thinking-block" aria-live="polite">
        <span class="chat-thinking-clock">${THINKING_CLOCK_ICON}</span>
        <div class="chat-thinking-content">
          ${lines}
          ${done}
        </div>
      </div>
    `;
  }

  function removeNode(node) {
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function clearSuggestions() {
    suggestionsEl.innerHTML = "";
    suggestionsEl.hidden = true;
  }

  function setSuggestions(items) {
    clearSuggestions();
    if (!items || !items.length) return;
    suggestionsEl.hidden = false;
    items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-chip";
      btn.textContent = item.label;
      btn.addEventListener("click", () => {
        if (busy) return;
        handleUserMessage(item.value, item.label);
      });
      suggestionsEl.appendChild(btn);
    });
  }

  function setComposerEnabled(enabled) {
    input.disabled = !enabled;
    sendBtn.disabled = !enabled;
  }

  function nextMissingStep() {
    const profile = bootstrap.profile || {};
    return (
      INTAKE_STEPS.find((step) => {
        if (step.optional) return false;
        const value = profile[step.id];
        return value == null || String(value).trim() === "";
      }) || null
    );
  }

  function askStep(step, options = {}) {
    currentStep = step;
    appendText("assistant", step.prompt, { persist: options.persist !== false });
    input.placeholder = step.placeholder || "Message Grants…";
    if (step.choiceKey) {
      const choices = (bootstrap.choices && bootstrap.choices[step.choiceKey]) || [];
      setSuggestions(
        choices.slice(0, step.choiceKey === "location_state" ? 12 : 20).map((c) => ({
          label: c.label,
          value: c.value,
        }))
      );
    } else if (step.optional) {
      setSuggestions([{ label: "Skip", value: "skip" }]);
    } else {
      clearSuggestions();
    }
    input.focus();
  }

  async function saveFields(fields, complete = false) {
    const response = await fetch(profileUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-CSRFToken": csrfToken,
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify({ fields, complete }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      const err =
        data.error === "validation_failed"
          ? "I still need a few required details before I can finish."
          : "I couldn't save that — try again.";
      throw new Error(err);
    }
    if (data.bootstrap) bootstrap = data.bootstrap;
    return data;
  }

  const sourceLabel = (source) => {
    if (source === "usaspending") return "USASpending";
    if (source === "granted_ai") return "GrantedAI";
    return "Grants.gov";
  };

  const deadlineVerb = (source) => (source === "usaspending" ? "Ends" : "Closes");

  const icons = {
    building: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4.5 20.5h15M7 20.5V6.8A1.3 1.3 0 0 1 8.3 5.5h7.4A1.3 1.3 0 0 1 17 6.8v13.7M10 9h1.2M10 12.5H11.2M13.8 9H15M13.8 12.5H15M10 16h4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`,
    pin: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 21s6-5.1 6-10.2A6 6 0 1 0 6 10.8C6 15.9 12 21 12 21Z" stroke="currentColor" stroke-width="1.7"/><circle cx="12" cy="10.5" r="2.2" stroke="currentColor" stroke-width="1.7"/></svg>`,
    mail: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3.5" y="5.5" width="17" height="13" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="m4.5 7.5 7.5 6 7.5-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    phone: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M8.2 4.8h2.3l1.1 3.3-1.5 1.1a11.5 11.5 0 0 0 5.2 5.2l1.1-1.5 3.3 1.1v2.3c0 .9-.7 1.7-1.6 1.7A13.7 13.7 0 0 1 4.8 6.4c0-.9.8-1.6 1.7-1.6Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>`,
    cash: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3.5" y="6.5" width="17" height="11" rx="2" stroke="currentColor" stroke-width="1.7"/><circle cx="12" cy="12" r="2.3" stroke="currentColor" stroke-width="1.7"/><path d="M7 12h.01M17 12h.01" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>`,
    calendar: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3.5" y="5.5" width="17" height="15" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="M3.5 10h17M8 3.5V7M16 3.5V7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`,
    status: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1.7"/><path d="m8.8 12.2 2.2 2.2 4.4-4.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    tag: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4.5 11V5.6c0-.6.5-1.1 1.1-1.1H11l8 8-6.5 6.5-8-8Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><circle cx="8.4" cy="8.4" r="1.2" fill="currentColor"/></svg>`,
  };

  function labelWithIcon(icon, text) {
    return `
      <span class="info-label">
        <span class="info-icon" aria-hidden="true">${icon}</span>
        ${text}
      </span>
    `;
  }

  function classifyContactPart(part) {
    const text = String(part || "").trim();
    if (!text) return null;
    if (text.includes("@")) return { type: "mail", text, icon: icons.mail };
    if (/phone\s*:/i.test(text) || /(\+?\d[\d\s().-]{6,}\d)/.test(text)) {
      return {
        type: "phone",
        text: text.replace(/^phone\s*:\s*/i, ""),
        icon: icons.phone,
      };
    }
    return { type: "pin", text, icon: icons.pin };
  }

  function renderContactChips(raw) {
    const parts = String(raw || "")
      .split("·")
      .map((part) => classifyContactPart(part))
      .filter(Boolean);
    if (!parts.length) return "";
    return `
      <div class="contact-chips">
        ${parts
          .map(
            (part) => `
          <span class="contact-chip contact-chip-${part.type}">
            <span class="contact-chip-icon" aria-hidden="true">${part.icon}</span>
            <span>${escapeHtml(part.text)}</span>
          </span>
        `
          )
          .join("")}
      </div>
    `;
  }

  function renderInfoRows(match) {
    const rows = [];
    const agency = match.agency || match.top_agency || "";
    if (agency) {
      rows.push(`
        <div class="info-row">
          ${labelWithIcon(icons.building, "Provided by")}
          <strong class="info-value">
            ${escapeHtml(agency)}
            ${match.agency_code ? `<span class="info-code">${escapeHtml(match.agency_code)}</span>` : ""}
          </strong>
          ${
            match.top_agency && match.top_agency !== match.agency
              ? `<span class="info-sub">${escapeHtml(match.top_agency)}</span>`
              : ""
          }
        </div>
      `);
    }
    if (match.agency_address) {
      rows.push(`
        <div class="info-row">
          ${labelWithIcon(icons.pin, "Address / contact")}
          ${renderContactChips(match.agency_address)}
        </div>
      `);
    }
    const facts = [];
    if (match.amount) {
      facts.push(`
        <div class="fact-item">
          ${labelWithIcon(icons.cash, "Amount")}
          <strong>${escapeHtml(match.amount)}</strong>
        </div>
      `);
    }
    if (match.deadline) {
      facts.push(`
        <div class="fact-item">
          ${labelWithIcon(icons.calendar, "Deadline")}
          <strong>${escapeHtml(deadlineVerb(match.source))} ${escapeHtml(match.deadline)}</strong>
        </div>
      `);
    }
    if (match.opp_status) {
      facts.push(`
        <div class="fact-item">
          ${labelWithIcon(icons.status, "Status")}
          <strong>${escapeHtml(match.opp_status)}</strong>
        </div>
      `);
    }
    if (facts.length) rows.push(`<div class="fact-grid">${facts.join("")}</div>`);
    return rows.join("") || `<div class="info-row"><span class="info-value-muted">Details unavailable</span></div>`;
  }

  function renderSaveControls(match) {
    if (match.is_saved) return `<span class="saved-pill">Saved</span>`;
    return `
      <form class="inline-save-form" method="post" action="${attr(saveUrl)}" data-no-loader="true">
        <input type="hidden" name="csrfmiddlewaretoken" value="${attr(csrfToken)}">
        <input type="hidden" name="next" value="${attr(homeUrl)}">
        <input type="hidden" name="source" value="${attr(match.source || "")}">
        <input type="hidden" name="external_id" value="${attr(match.save_external_id || "")}">
        <input type="hidden" name="title" value="${attr(match.title || "")}">
        <input type="hidden" name="agency" value="${attr(match.agency || "")}">
        <input type="hidden" name="agency_code" value="${attr(match.agency_code || "")}">
        <input type="hidden" name="agency_address" value="${attr(match.agency_address || "")}">
        <input type="hidden" name="top_agency" value="${attr(match.top_agency || "")}">
        <input type="hidden" name="deadline" value="${attr(match.deadline || "")}">
        <input type="hidden" name="url" value="${attr(match.url || "")}">
        <input type="hidden" name="opp_status" value="${attr(match.opp_status || "")}">
        <input type="hidden" name="number" value="${attr(match.number || "")}">
        <input type="hidden" name="amount" value="${attr(match.amount || "")}">
        <input type="hidden" name="category" value="${attr(match.category || "")}">
        <input type="hidden" name="score" value="${attr(match.score ?? "")}">
        <input type="hidden" name="reason" value="${attr(match.reason || "")}">
        <input type="hidden" name="description" value="${attr(match.description || "")}">
        <button type="submit" class="btn-save">Save</button>
      </form>
    `;
  }

  function matchKey(match) {
    return `${match.source || ""}:${match.save_external_id || match.id || match.number || match.url || match.title || ""}`;
  }

  function matchToolbarHtml() {
    return `
      <div class="chat-match-toolbar" hidden>
        <button type="button" class="chat-match-sort-btn" aria-pressed="false" title="Sort by match score">
          <span class="chat-match-sort-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none">
              <path d="M4 7h10M4 12h7M4 17h4M16 6v12M16 18l-3-3M16 6l3 3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </span>
          <span class="chat-match-sort-label">Sort by score</span>
        </button>
      </div>
    `;
  }

  function showMatchToolbar(row) {
    const toolbar = row && row.querySelector(".chat-match-toolbar");
    if (!toolbar) return;
    const cards = row.querySelectorAll(".match-card");
    toolbar.hidden = cards.length < 2;
  }

  function bindMatchToolbar(row, onReorder) {
    if (!row) return;
    const btn = row.querySelector(".chat-match-sort-btn");
    const matchesRoot = row.querySelector(".chat-matches");
    if (!btn || !matchesRoot || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";

    let sorted = false;
    let originalKeys = null;

    const labelEl = btn.querySelector(".chat-match-sort-label");

    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const cards = Array.from(matchesRoot.querySelectorAll(".match-card"));
      if (cards.length < 2) return;

      const anchor = captureScrollAnchor();

      if (!sorted) {
        originalKeys = cards.map((card) => card.dataset.matchKey || "");
        cards.sort((a, b) => {
          const scoreA = Number.parseFloat(a.querySelector(".score-badge")?.textContent || "") || 0;
          const scoreB = Number.parseFloat(b.querySelector(".score-badge")?.textContent || "") || 0;
          if (scoreB !== scoreA) return scoreB - scoreA;
          return (a.dataset.matchKey || "").localeCompare(b.dataset.matchKey || "");
        });
        cards.forEach((card) => matchesRoot.appendChild(card));
        sorted = true;
        btn.setAttribute("aria-pressed", "true");
        btn.title = "Restore original order";
        if (labelEl) labelEl.textContent = "Original order";
      } else {
        const byKey = new Map(
          cards.map((card) => [card.dataset.matchKey || "", card])
        );
        (originalKeys || []).forEach((key) => {
          const card = byKey.get(key);
          if (card) matchesRoot.appendChild(card);
        });
        sorted = false;
        originalKeys = null;
        btn.setAttribute("aria-pressed", "false");
        btn.title = "Sort by match score";
        if (labelEl) labelEl.textContent = "Sort by score";
      }

      if (typeof onReorder === "function") {
        const nextKeys = Array.from(matchesRoot.querySelectorAll(".match-card")).map(
          (card) => card.dataset.matchKey || ""
        );
        onReorder(nextKeys, sorted);
      }

      if (stickToBottom) scrollToBottom();
      else restoreScrollAnchor(anchor);
    });
  }

  function chanceMeta(match) {
    const score =
      match.score != null && match.score !== ""
        ? Number(match.score).toFixed(2)
        : "—";
    const chance =
      match.chance_percent != null
        ? match.chance_percent
        : Math.round((Number(match.score) || 0) * 100);
    const tier = String(match.chance_tier || "").toLowerCase() || "medium";
    const chanceLabel =
      match.chance_label ||
      (tier === "high"
        ? "High chance"
        : tier === "medium"
          ? "Medium chance"
          : tier === "low"
            ? "Lower chance"
            : `${chance}% chance`);
    return { score, chance, tier, chanceLabel };
  }

  function renderCard(match, index) {
    const { score, chance, tier, chanceLabel } = chanceMeta(match);
    const titleHtml = match.url
      ? `<a class="match-title-link" href="${attr(match.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(match.title || "Untitled grant")}</a>`
      : escapeHtml(match.title || "Untitled grant");
    const category = String(match.category || "").trim();
    const categoryHtml = category
      ? `<span class="category-pill" title="Category: ${attr(category)}"><span class="category-pill-icon" aria-hidden="true">${icons.tag}</span><span class="visually-hidden">Category:</span><span>${escapeHtml(category)}</span></span>`
      : "";
    const reason = String(match.reason || "").trim();

    return `
      <article class="match-card chance-${attr(tier)}" style="--i: ${index}" data-match-key="${attr(matchKey(match))}">
        <div class="score-badge" title="${attr(chanceLabel)} (${chance}%)" aria-label="Match score ${score}, ${chanceLabel}">${escapeHtml(score)}</div>
        <div class="match-content">
          <div class="match-head">
            <h2>${titleHtml}</h2>
            ${categoryHtml}
            <span class="chance-pill chance-${attr(tier)}">${escapeHtml(chanceLabel)}</span>
            <span class="source-pill source-${attr(match.source || "grants_gov")}">${escapeHtml(sourceLabel(match.source))}</span>
          </div>
          <div class="match-info">
            ${renderInfoRows(match)}
          </div>
          ${reason ? `<p class="match-reason">${escapeHtml(reason)}</p>` : `<p class="match-reason" hidden></p>`}
          <div class="match-footer">
            <div class="match-actions">
              ${match.url ? `<a class="btn-view" href="${attr(match.url)}" target="_blank" rel="noopener noreferrer">View</a>` : ""}
              ${renderSaveControls(match)}
            </div>
          </div>
        </div>
      </article>
    `;
  }

  function patchCardInPlace(card, match) {
    if (!card || !match) return;
    const { score, chance, tier, chanceLabel } = chanceMeta(match);
    card.classList.remove("chance-high", "chance-medium", "chance-low");
    card.classList.add(`chance-${tier}`);
    card.dataset.matchKey = matchKey(match);

    const badge = card.querySelector(".score-badge");
    if (badge) {
      badge.textContent = score;
      badge.title = `${chanceLabel} (${chance}%)`;
      badge.setAttribute("aria-label", `Match score ${score}, ${chanceLabel}`);
    }

    const chancePill = card.querySelector(".chance-pill");
    if (chancePill) {
      chancePill.className = `chance-pill chance-${tier}`;
      chancePill.textContent = chanceLabel;
    }

    const category = String(match.category || "").trim();
    let categoryPill = card.querySelector(".category-pill");
    const head = card.querySelector(".match-head");
    if (category && head) {
      if (categoryPill) {
        const label = categoryPill.querySelector("span:last-child");
        if (label) label.textContent = category;
        categoryPill.title = `Category: ${category}`;
      } else {
        const h2 = head.querySelector("h2");
        const html = `<span class="category-pill" title="Category: ${attr(category)}"><span class="category-pill-icon" aria-hidden="true">${icons.tag}</span><span class="visually-hidden">Category:</span><span>${escapeHtml(category)}</span></span>`;
        if (h2) h2.insertAdjacentHTML("afterend", html);
      }
    }

    const reasonEl = card.querySelector(".match-reason");
    const reason = String(match.reason || "").trim();
    if (reasonEl) {
      if (reason) {
        reasonEl.hidden = false;
        reasonEl.textContent = reason;
      }
    }

    const scoreInput = card.querySelector('input[name="score"]');
    if (scoreInput) scoreInput.value = match.score ?? "";
    const reasonInput = card.querySelector('input[name="reason"]');
    if (reasonInput) reasonInput.value = match.reason || "";
    const categoryInput = card.querySelector('input[name="category"]');
    if (categoryInput && category) categoryInput.value = category;
  }

  function updateSavedNavCount(count) {
    const link = document.querySelector('a.dash-nav-btn[href*="saved"]');
    if (!link || count == null) return;
    const icon = link.querySelector("svg");
    link.textContent = "";
    if (icon) link.appendChild(icon);
    link.appendChild(document.createTextNode(` Saved (${count})`));
  }

  function bindSaveForms(root) {
    root.querySelectorAll(".inline-save-form").forEach((saveForm) => {
      saveForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const button = saveForm.querySelector(".btn-save");
        if (button) {
          button.disabled = true;
          button.textContent = "Saving…";
        }
        try {
          const response = await fetch(saveForm.action, {
            method: "POST",
            body: new FormData(saveForm),
            headers: {
              Accept: "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "same-origin",
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || !data.ok) throw new Error("save_failed");
          saveForm.replaceWith(
            Object.assign(document.createElement("span"), {
              className: "saved-pill",
              textContent: "Saved",
            })
          );
          if (typeof data.saved_count === "number") {
            updateSavedNavCount(data.saved_count);
          }
        } catch (_) {
          if (button) {
            button.disabled = false;
            button.textContent = "Save";
          }
        }
      });
    });
  }

  function placeTextFrom(location) {
    const city = location?.city || "";
    const state = location?.state || "";
    const place = [city, state].filter(Boolean).join(", ");
    return place ? ` for ${place}` : "";
  }

  async function readSseEvents(response, onEvent) {
    if (!response.body) throw new Error("no_stream");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const dataLine = chunk
          .split("\n")
          .map((line) => line.trimEnd())
          .find((line) => line.startsWith("data:"));
        if (!dataLine) continue;
        const raw = dataLine.replace(/^data:\s?/, "");
        if (!raw || raw === "[DONE]") continue;
        let event;
        try {
          event = JSON.parse(raw);
        } catch (_) {
          /* ignore malformed chunk */
          continue;
        }
        await onEvent(event);
      }
    }
  }

  async function loadMatches(userQuery = "") {
    keepBusy = true;
    clearSuggestions();
    currentStep = null;
    input.placeholder = "Message Grants…";
    setComposerEnabled(false);
    // Pin for the initial search kickoff; unlock once the first cards appear
    // so later SSE updates don't yank a reader away from card #1.
    stickToBottom = true;

    // Show the loader immediately — before sync/network — so Find Grants feels instant.
    let statusRow = showTyping();
    let statusBubble = statusRow.querySelector(".chat-bubble");
    scrollToBottom({ force: true });

    const callingLabels = [];
    const noteAgentCalling = (message) => {
      const raw = String(message || "").trim();
      // Support single or multi-line "Agent calling X…" payloads.
      const lines = raw.split(/\n+/).map((line) => line.trim()).filter(Boolean);
      let sawCalling = false;
      for (const line of lines) {
        const match = line.match(/^Agent calling\s+(.+?)(?:…|\.\.\.)?\s*$/i);
        if (!match) continue;
        sawCalling = true;
        const label = match[1].replace(/[.…]+$/g, "").trim();
        if (label && !callingLabels.includes(label)) callingLabels.push(label);
      }
      if (!sawCalling) return null;
      return callingLabels.map((label) => `Agent calling ${label}…`).join("\n");
    };

    await syncProjectSnapshot();
    const queryText = String(userQuery || "").trim();

    let resultsRow = null;
    let summaryEl = null;
    let matchesEl = null;
    let seenKeys = new Set();
    let displayedMatches = [];
    let cardIndex = 0;
    let finished = false;
    let hasVisibleCards = false;
    let rankingNote = "";

    const setStatus = async (text) => {
      const callingText = noteAgentCalling(text);
      const merged = callingText || String(text || "").trim();
      if (!statusBubble) {
        statusRow = await appendText("assistant", "", { persist: false });
        statusBubble = statusRow.querySelector(".chat-bubble");
      }
      if (!statusBubble) return;

      const anchor = captureScrollAnchor();
      if (callingText) {
        statusBubble.classList.add("chat-status-calling");
        statusBubble.style.whiteSpace = "normal";
        statusBubble.innerHTML = renderThinkingHtml(callingText);
      } else {
        statusBubble.classList.remove("chat-status-calling");
        statusBubble.style.whiteSpace = "pre-line";
        statusBubble.textContent = merged;
      }
      if (hasVisibleCards) restoreScrollAnchor(anchor);
      else {
        restoreScrollAnchor(anchor);
        scrollToBottom();
      }
    };

    const markThinkingDone = () => {
      if (!statusBubble || !statusBubble.classList.contains("chat-status-calling")) {
        return;
      }
      const content = statusBubble.querySelector(".chat-thinking-content");
      if (!content || content.querySelector(".chat-thinking-done")) return;
      content.insertAdjacentHTML(
        "beforeend",
        `<div class="chat-thinking-done"><span class="chat-thinking-done-icon">${THINKING_DONE_ICON}</span><span>Done</span></div>`
      );
    };

    const ensureResultsShell = async (placeText) => {
      if (resultsRow) return;
      const anchor = captureScrollAnchor();
      resultsRow = await appendAssistantHtml(
        `<p class="chat-match-summary">Gathering ranked opportunities${escapeHtml(placeText)}…</p>
         <div class="chat-matches"></div>
         <p class="chat-match-progress" hidden></p>
         ${matchToolbarHtml()}`,
        { persist: false }
      );
      summaryEl = resultsRow.querySelector(".chat-match-summary");
      matchesEl = resultsRow.querySelector(".chat-matches");
      bindMatchToolbar(resultsRow, (nextKeys) => {
        if (!Array.isArray(nextKeys) || !nextKeys.length) return;
        const byKey = new Map(
          displayedMatches.map((row) => [matchKey(row), row])
        );
        displayedMatches = nextKeys
          .map((key) => byKey.get(key))
          .filter(Boolean);
      });
      if (hasVisibleCards) {
        restoreScrollAnchor(anchor);
      } else {
        scrollToBottom();
      }
    };

    const setProgressNote = (text) => {
      if (!resultsRow) return;
      const noteEl = resultsRow.querySelector(".chat-match-progress");
      if (!noteEl) return;
      const callingText = noteAgentCalling(text);
      rankingNote = (callingText || String(text || "")).trim();
      if (!rankingNote) {
        noteEl.hidden = true;
        noteEl.textContent = "";
        noteEl.classList.remove("chat-status-calling");
        return;
      }
      noteEl.hidden = false;
      if (callingText) {
        noteEl.classList.add("chat-status-calling");
        noteEl.style.whiteSpace = "normal";
        noteEl.innerHTML = renderThinkingHtml(callingText);
      } else {
        noteEl.classList.remove("chat-status-calling");
        noteEl.style.whiteSpace = "pre-line";
        noteEl.textContent = rankingNote;
      }
    };

    const appendMatchCards = (matches) => {
      if (!matchesEl || !Array.isArray(matches)) return;
      const beforeCount = cardIndex;
      const anchor = captureScrollAnchor();
      for (const match of matches) {
        const key = matchKey(match);
        if (!key || seenKeys.has(key)) continue;
        seenKeys.add(key);
        displayedMatches.push(match);
        matchesEl.insertAdjacentHTML("beforeend", renderCard(match, cardIndex));
        cardIndex += 1;
      }
      if (cardIndex === beforeCount) return;
      bindSaveForms(resultsRow);
      if (!hasVisibleCards) {
        hasVisibleCards = true;
        markThinkingDone();
        // Bring the first cards into view, then unlock so later SSE chunks
        // cannot pull the reader to the bottom while they read card #1.
        scrollToBottom();
        stickToBottom = false;
        return;
      }
      if (stickToBottom) {
        scrollToBottom();
      } else {
        restoreScrollAnchor(anchor);
      }
    };

    const renderFinal = async (matches, location, savedCount) => {
      const placeText = placeTextFrom(location);
      const finalMatches = Array.isArray(matches) ? matches : [];
      if (!finalMatches.length && !displayedMatches.length) {
        const emptyAnchor = captureScrollAnchor();
        if (resultsRow) removeNode(resultsRow);
        const emptyMsg = `I couldn't find ranked matches yet${placeText}. You can update your project details in chat, then ask me to search again.`;
        await setStatus(emptyMsg);
        restoreScrollAnchor(emptyAnchor);
        await persistMessage("assistant", emptyMsg);
        if (stickToBottom) scrollToBottom();
        return;
      }
      await ensureResultsShell(placeText);
      const anchor = captureScrollAnchor();

      // Merge AI scores onto the same cards in arrival order — never reshuffle DOM.
      const byKey = new Map();
      for (const match of finalMatches) {
        const key = matchKey(match);
        if (key) byKey.set(key, match);
      }

      if (matchesEl) {
        matchesEl.querySelectorAll(".match-card").forEach((card) => {
          const key = card.dataset.matchKey || "";
          const updated = byKey.get(key);
          if (!updated) return;
          patchCardInPlace(card, updated);
          const idx = displayedMatches.findIndex((row) => matchKey(row) === key);
          if (idx >= 0) displayedMatches[idx] = { ...displayedMatches[idx], ...updated };
        });
      }

      // Append any final-only rows at the end (still no reordering of existing cards).
      const missing = finalMatches.filter((match) => {
        const key = matchKey(match);
        return key && !seenKeys.has(key);
      });
      if (missing.length) appendMatchCards(missing);

      const total = displayedMatches.length || finalMatches.length;
      const noun = total === 1 ? "opportunity" : "opportunities";
      const summary = `Here are ${total} ${noun} from Grants.gov, USASpending, and GrantedAI${placeText}.`;
      if (summaryEl) summaryEl.textContent = summary;
      setProgressNote("");
      hasVisibleCards = true;
      bindSaveForms(resultsRow);
      showMatchToolbar(resultsRow);
      if (typeof savedCount === "number") updateSavedNavCount(savedCount);
      markThinkingDone();
      if (statusRow) removeNode(statusRow);
      statusRow = null;
      statusBubble = null;
      if (stickToBottom) {
        scrollToBottom();
      } else {
        restoreScrollAnchor(anchor);
      }
      await persistMessage("assistant", summary, {
        type: "matches",
        // Persist stable on-screen order (arrival order + score patches).
        matches: displayedMatches.length ? displayedMatches : finalMatches,
        location: location || {},
      });
    };

    try {
      const streamUrl = new URL(matchesStreamUrl, window.location.origin);
      if (queryText) streamUrl.searchParams.set("q", queryText);
      const response = await fetch(streamUrl.toString(), {
        headers: { Accept: "text/event-stream" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      await readSseEvents(response, async (event) => {
        const type = event.type || "";
        if (type === "status") {
          const message = event.message || "Searching…";
          const callingText = noteAgentCalling(message);
          const display = callingText || message;
          if (hasVisibleCards) {
            setProgressNote(display);
            const placeText = placeTextFrom(event.location || {});
            if (summaryEl && !callingText) {
              const anchor = captureScrollAnchor();
              summaryEl.textContent = `Ranking opportunities${placeText}…`;
              restoreScrollAnchor(anchor);
            }
          } else {
            await setStatus(display);
          }
          return;
        }
        if (type === "source") {
          const placeText = placeTextFrom(event.location || {});
          await ensureResultsShell(placeText);
          const incoming = Array.isArray(event.matches) ? event.matches : [];
          if (incoming.length) {
            appendMatchCards(incoming);
            if (summaryEl && hasVisibleCards) {
              const anchor = captureScrollAnchor();
              const noun = cardIndex === 1 ? "opportunity" : "opportunities";
              summaryEl.textContent = `Showing ${cardIndex} ${noun} so far${placeText}. More sources still loading…`;
              if (!stickToBottom) restoreScrollAnchor(anchor);
            }
          }
          const statusMsg = event.message || "Still searching…";
          if (hasVisibleCards) {
            setProgressNote(statusMsg);
          } else {
            await setStatus(statusMsg);
          }
          if (typeof event.saved_count === "number") {
            updateSavedNavCount(event.saved_count);
          }
          return;
        }
        if (type === "done") {
          finished = true;
          await renderFinal(
            Array.isArray(event.matches) ? event.matches : [],
            event.location || {},
            event.saved_count
          );
          return;
        }
        if (type === "error") {
          throw new Error(event.message || "match_failed");
        }
      });

      if (!finished) {
        // Fallback if stream ended without a done event.
        const fallbackUrl = new URL(matchesUrl, window.location.origin);
        if (queryText) fallbackUrl.searchParams.set("q", queryText);
        const fallback = await fetch(fallbackUrl.toString(), {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const data = await fallback.json().catch(() => ({}));
        if (!fallback.ok) throw new Error(data.error || "match_failed");
        await renderFinal(
          Array.isArray(data.matches) ? data.matches : [],
          data.location || {},
          data.saved_count
        );
      }

      setSuggestions([
        { label: "Find grants again", value: "find grants" },
        { label: "Update my project", value: "update my project" },
      ]);
    } catch (_) {
      if (statusRow) removeNode(statusRow);
      if (resultsRow) removeNode(resultsRow);
      appendText(
        "assistant",
        "Something went wrong while ranking grants. Try again in a moment."
      );
      setSuggestions([{ label: "Try again", value: "find grants" }]);
    } finally {
      busy = false;
      keepBusy = false;
      setComposerEnabled(true);
      input.focus();
    }
  }

  async function finishIntake() {
    keepBusy = true;
    const typing = showTyping();
    try {
      await saveFields({}, true);
      await syncProjectSnapshot();
      removeNode(typing);
      appendText(
        "assistant",
        "Thanks — your project profile is ready. I'll search Grants.gov, USASpending, and GrantedAI now."
      );
      await loadMatches();
    } catch (err) {
      removeNode(typing);
      appendText("assistant", err.message || "I couldn't finish setup.");
      const missing = nextMissingStep();
      if (missing) askStep(missing);
      busy = false;
      keepBusy = false;
      setComposerEnabled(true);
    }
  }

  function resolveChoice(step, text) {
    if (!step.choiceKey) return text;
    const choices = (bootstrap.choices && bootstrap.choices[step.choiceKey]) || [];
    const normalized = text.trim().toLowerCase();
    const byValue = choices.find((c) => String(c.value).toLowerCase() === normalized);
    if (byValue) return byValue.value;
    const byLabel = choices.find((c) => String(c.label).toLowerCase() === normalized);
    if (byLabel) return byLabel.value;
    if (step.id === "location_state" && /^[a-z]{2}$/i.test(text.trim())) {
      return text.trim().toUpperCase();
    }
    return text.trim();
  }

  async function handleIntakeAnswer(rawText, displayText) {
    const step = currentStep;
    if (!step) return;

    const trimmed = String(rawText || "").trim();
    await appendText("user", displayText || trimmed);
    clearSuggestions();

    if (step.optional && /^(skip|none|n\/a|na|-)$/i.test(trimmed)) {
      bootstrap.profile[step.id] = "";
      await saveFields({ [step.id]: "" });
    } else {
      if (!trimmed) {
        await appendText("assistant", "I need a little more detail on that one.");
        askStep(step);
        return;
      }
      let value = resolveChoice(step, trimmed);
      if (step.id === "budget_requested") {
        value = trimmed.replace(/[$,]/g, "").trim();
        if (!value || Number.isNaN(Number(value))) {
          await appendText(
            "assistant",
            "Please enter a number for the budget, like 50000."
          );
          askStep(step);
          return;
        }
      }
      if (step.choiceKey) {
        const choices = (bootstrap.choices && bootstrap.choices[step.choiceKey]) || [];
        const ok = choices.some((c) => c.value === value);
        if (!ok && step.id !== "location_state") {
          await appendText(
            "assistant",
            "Please pick one of the options, or type it exactly."
          );
          askStep(step);
          return;
        }
      }
      try {
        await saveFields({ [step.id]: value });
        bootstrap.profile[step.id] = value;
      } catch (err) {
        await appendText("assistant", err.message || "Couldn't save that.");
        askStep(step);
        return;
      }
    }

    // Walk sequential steps so optional ones still get asked once.
    const stepIndex = INTAKE_STEPS.findIndex((s) => s.id === step.id);
    const sequential = INTAKE_STEPS[stepIndex + 1];
    if (sequential) {
      askStep(sequential);
      return;
    }

    await finishIntake();
  }

  // Only an explicit "update/change/edit my project/profile/intake" request
  // re-opens intake. Everything else from an onboarded user is a grant search.
  function wantsUpdateProject(text) {
    return /\b(update|change|edit|redo)\b.*\b(project|profile|details|intake)\b/i.test(
      text
    ) || /^update my project$/i.test(text);
  }

  // Starter card "Update my project" — open intake directly, independent of the
  // card's (customizable) label text.
  async function startProjectUpdate(displayText) {
    await appendText("user", displayText || "Update my project");
    clearSuggestions();
    await appendText(
      "assistant",
      "Sure — let's refresh your project details. You can also edit everything later in Settings."
    );
    askStep(INTAKE_STEPS[0]);
  }

  // Starter search card — show the card label in the thread, but run the search
  // with the card's configured query so it passes to the model with the saved
  // profile (same override logic as a typed message).
  async function runStarterSearch(card) {
    busy = true;
    keepBusy = true;
    setComposerEnabled(false);
    const query = String(card.query || card.title || "").trim();
    const display = card.title || query;
    // Paint the user bubble immediately; persist in the background so the
    // loader is not blocked on the messages API.
    await appendText("user", display, { persist: false });
    clearSuggestions();
    const persistUser = persistMessage("user", display).catch(() => {});
    try {
      await loadMatches(query);
    } finally {
      await persistUser;
    }
  }

  async function handleReadyMessage(text) {
    await appendText("user", text);
    clearSuggestions();

    if (wantsUpdateProject(text)) {
      await appendText(
        "assistant",
        "Sure — let's refresh your project details. You can also edit everything later in Settings."
      );
      askStep(INTAKE_STEPS[0]);
      return;
    }

    // Onboarding is complete: treat every other message as a grant search and
    // pass the raw text as the query. The backend (resolve_search_context)
    // applies any location / topic / budget / org overrides on top of the saved
    // profile — so "find in california" returns California grants, while a bare
    // "find grants" uses the saved project profile defaults. Never stop to ask
    // "what would you like to do?"; just run the search.
    await loadMatches(text);
  }

  async function handleUserMessage(value, displayText) {
    const text = String(value || "").trim();
    if (!text || busy) return;
    hideStarter();
    busy = true;
    keepBusy = false;
    setComposerEnabled(false);

    try {
      if (currentStep || !bootstrap.onboarding_completed) {
        if (!currentStep) {
          askStep(nextMissingStep() || INTAKE_STEPS[0]);
          return;
        }
        await handleIntakeAnswer(text, displayText);
        return;
      }
      await handleReadyMessage(displayText || text);
    } finally {
      if (!keepBusy) {
        busy = false;
        setComposerEnabled(true);
        input.focus();
      }
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    autosize();
    handleUserMessage(text);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  input.addEventListener("input", autosize);

  if (newChatBtn) {
    newChatBtn.addEventListener("click", () => {
      createNewChat();
    });
  }
  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", () => {
      if (isMobileSidebar()) {
        if (document.body.classList.contains("chat-sidebar-open")) {
          closeSidebar();
        } else {
          openSidebar();
        }
        return;
      }
      openSidebar();
    });
  }
  if (sidebarClose) {
    sidebarClose.addEventListener("click", () => {
      closeSidebar();
    });
  }
  if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener("click", closeSidebar);
  }

  // Restore desktop collapsed preference.
  try {
    if (!isMobileSidebar() && localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1") {
      setSidebarCollapsed(true);
    }
  } catch (_) {
    /* ignore */
  }

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Node)) return;
    if (listEl && listEl.contains(target) && target.closest(".chat-conversation-menu")) {
      return;
    }
    closeAllConversationMenus();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAllConversationMenus();
  });

  renderConversationList();
  if (conversationId) {
    setActiveTitle(
      (
        conversations.find((row) => isSameConversation(row.id, conversationId)) ||
        {}
      ).title || "New chat"
    );
    loadConversation(conversationId, { force: true });
  } else {
    createNewChat();
  }
  autosize();
})();
