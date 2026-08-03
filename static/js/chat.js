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
  let conversationId =
    Number(app.dataset.activeConversationId || conversationsState.active_id) ||
    null;
  let conversations = Array.isArray(conversationsState.conversations)
    ? conversationsState.conversations
    : [];
  let busy = false;
  let keepBusy = false;
  let currentStep = null;
  let persistEnabled = true;

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

  function scrollToBottom() {
    transcript.scrollTop = transcript.scrollHeight;
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
    scrollToBottom();
    return row;
  }

  function appendText(role, text, options = {}) {
    const row = appendRow(
      role,
      `<div class="chat-bubble">${escapeHtml(text)}</div>`
    );
    if (options.persist !== false) {
      persistMessage(role, text, options.metadata || {});
    }
    return row;
  }

  function appendAssistantHtml(inner, options = {}) {
    const row = appendRow("assistant", `<div class="chat-bubble">${inner}</div>`);
    if (options.persist !== false && options.persistContent) {
      persistMessage("assistant", options.persistContent, options.metadata || {});
    }
    return row;
  }

  async function persistMessage(role, content, metadata = {}) {
    if (!persistEnabled || !conversationId) return null;
    const text = String(content || "").trim();
    if (!text && !(metadata && Object.keys(metadata).length)) return null;
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
    const rest = conversations.filter((row) => row.id !== conversation.id);
    conversations = [conversation, ...rest];
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

  async function deleteConversation(id) {
    if (!id || busy) return;
    const confirmed = window.confirm("Delete this chat? This cannot be undone.");
    if (!confirmed) return;

    busy = true;
    setComposerEnabled(false);
    closeAllConversationMenus();
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
      const wasActive = id === conversationId;
      renderConversationList();

      if (!wasActive) return;

      if (data.next_id) {
        await loadConversation(data.next_id);
      } else {
        conversationId = null;
        await createNewChat();
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
      const wrap = document.createElement("div");
      wrap.className = `chat-conversation-row${
        row.id === conversationId ? " is-active" : ""
      }`;
      wrap.dataset.conversationId = String(row.id);

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-conversation-item";
      btn.textContent = row.title || "New chat";
      btn.title = row.title || "New chat";
      btn.addEventListener("click", () => {
        if (row.id === conversationId || busy) return;
        closeAllConversationMenus();
        loadConversation(row.id);
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
        deleteConversation(row.id);
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
      ? [
          {
            title: "Find grants",
            description: "Search by focus & location",
            value: "find grants",
            tone: "forest",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="6.25" stroke="currentColor" stroke-width="1.7"/><path d="m16.2 16.2 3.3 3.3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`,
          },
          {
            title: "Update my project",
            description: "Refresh intake details",
            value: "update my project",
            tone: "sage",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M14.2 5.8 18.2 9.8M5.5 18.5l1.1-4.2L16.1 4.8a1.4 1.4 0 0 1 2 0l1.1 1.1a1.4 1.4 0 0 1 0 2L9.7 17.4 5.5 18.5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>`,
          },
          {
            title: "View saved",
            description: "Open grants you saved",
            href: "/accounts/saved/",
            tone: "gold",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 4.5h10a1 1 0 0 1 1 1V20l-6-3.2L6 20V5.5a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>`,
          },
          {
            title: "Ask anything",
            description: "Type a question below",
            focusInput: true,
            tone: "olive",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5.5 6.5h13A2.5 2.5 0 0 1 21 9v6.2a2.5 2.5 0 0 1-2.5 2.5H12l-3.8 2.6V17.7H5.5A2.5 2.5 0 0 1 3 15.2V9a2.5 2.5 0 0 1 2.5-2.5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>`,
          },
        ]
      : [
          {
            title: "Start project setup",
            description: "Answer a few quick questions",
            value: "start setup",
            tone: "forest",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 7h14M5 12h14M5 17h9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
          },
          {
            title: "What you’ll get",
            description: "Ranked matches from 3 sources",
            value: "start setup",
            tone: "sage",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="6.5" stroke="currentColor" stroke-width="1.8"/><path d="m16 16 4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
          },
          {
            title: "View saved",
            description: "Open grants you saved",
            href: "/accounts/saved/",
            tone: "gold",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 4.5h10a1 1 0 0 1 1 1V20l-6-3.2L6 20V5.5a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`,
          },
          {
            title: "Ask anything",
            description: "Type a question below",
            focusInput: true,
            tone: "olive",
            icon: `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12h10M12 6l6 6-6 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
          },
        ];

    starterGridEl.innerHTML = "";
    cards.forEach((card) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-starter-card";
      btn.innerHTML = `
        <span class="chat-starter-card-icon is-${card.tone}" aria-hidden="true">${card.icon}</span>
        <span class="chat-starter-card-copy">
          <strong>${escapeHtml(card.title)}</strong>
          <span>${escapeHtml(card.description)}</span>
        </span>
      `;
      btn.addEventListener("click", () => {
        if (busy) return;
        if (card.href) {
          window.location.href = card.href;
          return;
        }
        if (card.focusInput) {
          hideStarter();
          setSuggestions([
            { label: "Find grants", value: "find grants" },
            { label: "Update my project", value: "update my project" },
          ]);
          input.focus();
          return;
        }
        hideStarter();
        if (card.value === "start setup") {
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
        handleUserMessage(card.value, card.title);
      });
      starterGridEl.appendChild(btn);
    });

    starterEl.hidden = false;
    app.classList.add("is-starter");
    clearSuggestions();
  }

  function replayMessage(message) {
    const role = message.role === "user" ? "user" : "assistant";
    const meta = message.metadata || {};
    if (role === "assistant" && Array.isArray(meta.matches) && meta.matches.length) {
      const summary =
        message.content ||
        `Here are ${meta.matches.length} ranked opportunities.`;
      const row = appendAssistantHtml(
        `<p class="chat-match-summary">${escapeHtml(summary)}</p>
         <div class="chat-matches">${meta.matches
           .map((m, i) => renderCard(m, i))
           .join("")}</div>`,
        { persist: false }
      );
      bindSaveForms(row);
      return;
    }
    appendText(role, message.content || "", { persist: false });
  }

  async function loadConversation(id) {
    if (!id) return;
    busy = true;
    setComposerEnabled(false);
    clearTranscript();
    try {
      const response = await fetch(`${conversationsUrl}${id}/`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error("load_failed");

      conversationId = data.conversation.id;
      upsertConversationLocal(data.conversation);
      if (data.bootstrap) bootstrap = data.bootstrap;
      setActiveTitle(data.conversation.title);
      renderConversationList();

      const messages = Array.isArray(data.messages) ? data.messages : [];
      if (!messages.length) {
        persistEnabled = true;
        startFreshGreeting({ persist: true });
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
      appendText(
        "assistant",
        "I couldn't load that conversation. Try another one or start a new chat.",
        { persist: false }
      );
    } finally {
      busy = false;
      setComposerEnabled(true);
      input.focus();
    }
  }

  async function createNewChat() {
    if (busy) return;
    busy = true;
    setComposerEnabled(false);
    try {
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
      if (!response.ok || !data.ok) throw new Error("create_failed");
      conversationId = data.conversation.id;
      upsertConversationLocal(data.conversation);
      renderConversationList();
      setActiveTitle(data.conversation.title);
      clearTranscript();
      startFreshGreeting({ persist: true });
      closeSidebarMobile();
    } catch (_) {
      appendText(
        "assistant",
        "I couldn't start a new chat just now. Please try again.",
        { persist: false }
      );
    } finally {
      busy = false;
      setComposerEnabled(true);
      input.focus();
    }
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
        <input type="hidden" name="score" value="${attr(match.score ?? "")}">
        <input type="hidden" name="reason" value="${attr(match.reason || "")}">
        <input type="hidden" name="description" value="${attr(match.description || "")}">
        <button type="submit" class="btn-save">Save</button>
      </form>
    `;
  }

  function renderCard(match, index) {
    const score =
      match.score != null && match.score !== ""
        ? Number(match.score).toFixed(2)
        : "—";
    const chance =
      match.chance_percent != null
        ? match.chance_percent
        : Math.round((Number(match.score) || 0) * 100);
    const tier = String(match.chance_tier || "").toLowerCase();
    const chanceLabel =
      match.chance_label ||
      (tier === "high"
        ? "High chance"
        : tier === "medium"
          ? "Medium chance"
          : tier === "low"
            ? "Lower chance"
            : `${chance}% chance`);
    const titleHtml = match.url
      ? `<a class="match-title-link" href="${attr(match.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(match.title || "Untitled grant")}</a>`
      : escapeHtml(match.title || "Untitled grant");

    return `
      <article class="match-card chance-${attr(tier || "medium")}" style="--i: ${index}">
        <div class="score-badge" title="${attr(chanceLabel)} (${chance}%)" aria-label="Match score ${score}, ${chanceLabel}">${escapeHtml(score)}</div>
        <div class="match-content">
          <div class="match-head">
            <h2>${titleHtml}</h2>
            <span class="chance-pill chance-${attr(tier || "medium")}">${escapeHtml(chanceLabel)}</span>
            <span class="source-pill source-${attr(match.source || "grants_gov")}">${escapeHtml(sourceLabel(match.source))}</span>
          </div>
          <div class="match-info">
            ${renderInfoRows(match)}
          </div>
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
        try {
          onEvent(JSON.parse(raw));
        } catch (_) {
          /* ignore malformed chunk */
        }
      }
    }
  }

  async function loadMatches() {
    keepBusy = true;
    clearSuggestions();
    currentStep = null;
    input.placeholder = "Message Grants…";
    setComposerEnabled(false);
    await syncProjectSnapshot();

    let statusRow = showTyping();
    let statusBubble = statusRow.querySelector(".chat-bubble");
    let resultsRow = null;
    let summaryEl = null;
    let matchesEl = null;
    let seenKeys = new Set();
    let cardIndex = 0;
    let finished = false;

    const setStatus = (text) => {
      if (!statusBubble) {
        statusRow = appendText("assistant", text);
        statusBubble = statusRow.querySelector(".chat-bubble");
        return;
      }
      statusBubble.textContent = text;
      scrollToBottom();
    };

    const ensureResultsShell = (placeText) => {
      if (resultsRow) return;
      resultsRow = appendAssistantHtml(
        `<p class="chat-match-summary">Gathering ranked opportunities${escapeHtml(placeText)}…</p>
         <div class="chat-matches"></div>`
      );
      summaryEl = resultsRow.querySelector(".chat-match-summary");
      matchesEl = resultsRow.querySelector(".chat-matches");
    };

    const appendMatchCards = (matches) => {
      if (!matchesEl || !Array.isArray(matches)) return;
      for (const match of matches) {
        const key = `${match.source || ""}:${match.save_external_id || match.id || match.number || match.url || match.title || ""}`;
        if (seenKeys.has(key)) continue;
        seenKeys.add(key);
        matchesEl.insertAdjacentHTML("beforeend", renderCard(match, cardIndex));
        cardIndex += 1;
      }
      bindSaveForms(resultsRow);
      scrollToBottom();
    };

    const renderFinal = (matches, location, savedCount) => {
      const placeText = placeTextFrom(location);
      if (!matches.length) {
        if (resultsRow) removeNode(resultsRow);
        const emptyMsg = `I couldn't find ranked matches yet${placeText}. You can update your project details in chat, then ask me to search again.`;
        setStatus(emptyMsg);
        persistMessage("assistant", emptyMsg);
        return;
      }
      ensureResultsShell(placeText);
      const noun = matches.length === 1 ? "opportunity" : "opportunities";
      const summary = `Here are ${matches.length} ranked ${noun} from Grants.gov, USASpending, and GrantedAI${placeText}.`;
      if (summaryEl) {
        summaryEl.textContent = summary;
      }
      seenKeys = new Set();
      cardIndex = 0;
      matchesEl.innerHTML = matches.map((m, i) => renderCard(m, i)).join("");
      bindSaveForms(resultsRow);
      if (typeof savedCount === "number") updateSavedNavCount(savedCount);
      if (statusRow) removeNode(statusRow);
      statusRow = null;
      statusBubble = null;
      persistMessage("assistant", summary, {
        type: "matches",
        matches,
        location: location || {},
      });
      scrollToBottom();
    };

    try {
      const response = await fetch(matchesStreamUrl, {
        headers: { Accept: "text/event-stream" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      await readSseEvents(response, (event) => {
        const type = event.type || "";
        if (type === "status") {
          setStatus(event.message || "Searching…");
          return;
        }
        if (type === "source") {
          setStatus(event.message || "Still searching…");
          const placeText = placeTextFrom(event.location || {});
          ensureResultsShell(placeText);
          appendMatchCards(event.matches || []);
          if (typeof event.saved_count === "number") {
            updateSavedNavCount(event.saved_count);
          }
          return;
        }
        if (type === "done") {
          finished = true;
          renderFinal(
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
        const fallback = await fetch(matchesUrl, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const data = await fallback.json().catch(() => ({}));
        if (!fallback.ok) throw new Error(data.error || "match_failed");
        renderFinal(
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
    appendText("user", displayText || trimmed);
    clearSuggestions();

    if (step.optional && /^(skip|none|n\/a|na|-)$/i.test(trimmed)) {
      bootstrap.profile[step.id] = "";
      await saveFields({ [step.id]: "" });
    } else {
      if (!trimmed) {
        appendText("assistant", "I need a little more detail on that one.");
        askStep(step);
        return;
      }
      let value = resolveChoice(step, trimmed);
      if (step.id === "budget_requested") {
        value = trimmed.replace(/[$,]/g, "").trim();
        if (!value || Number.isNaN(Number(value))) {
          appendText("assistant", "Please enter a number for the budget, like 50000.");
          askStep(step);
          return;
        }
      }
      if (step.choiceKey) {
        const choices = (bootstrap.choices && bootstrap.choices[step.choiceKey]) || [];
        const ok = choices.some((c) => c.value === value);
        if (!ok && step.id !== "location_state") {
          appendText("assistant", "Please pick one of the options, or type it exactly.");
          askStep(step);
          return;
        }
      }
      try {
        await saveFields({ [step.id]: value });
        bootstrap.profile[step.id] = value;
      } catch (err) {
        appendText("assistant", err.message || "Couldn't save that.");
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

  function wantsFindGrants(text) {
    return /\b(find|search|match|show|get)\b.*\bgrant/i.test(text) ||
      /^(find grants|search again|try again|match me|show matches)$/i.test(text);
  }

  function wantsUpdateProject(text) {
    return /\b(update|change|edit|redo)\b.*\b(project|profile|details|intake)\b/i.test(
      text
    ) || /^update my project$/i.test(text);
  }

  async function handleReadyMessage(text) {
    appendText("user", text);
    clearSuggestions();

    if (wantsUpdateProject(text)) {
      appendText(
        "assistant",
        "Sure — let's refresh your project details. You can also edit everything later in Settings."
      );
      askStep(INTAKE_STEPS[0]);
      return;
    }

    if (wantsFindGrants(text) || /^(yes|yeah|yep|ok|okay|sure|go ahead)$/i.test(text)) {
      await loadMatches();
      return;
    }

    appendText(
      "assistant",
      "I can find grant matches from your saved project profile, or walk you through updating those details. What would you like to do?"
    );
    setSuggestions([
      { label: "Find grants", value: "find grants" },
      { label: "Update my project", value: "update my project" },
    ]);
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
  setActiveTitle(
    (conversations.find((row) => row.id === conversationId) || {}).title ||
      "New chat"
  );
  if (conversationId) {
    loadConversation(conversationId);
  } else {
    createNewChat();
  }
  autosize();
})();
