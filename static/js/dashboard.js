(() => {
  const board = document.getElementById("match-board");
  const subtitle = document.getElementById("dash-subtitle");
  if (!board) return;

  const apiUrl = board.dataset.matchesUrl || "/home/matches/";
  const saveUrl = board.dataset.saveUrl || "/accounts/saved/add/";
  const homeUrl = board.dataset.homeUrl || "/home/";
  const csrfToken =
    board.dataset.csrfToken ||
    (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] ||
    "";

  const sourceLabel = (source) => {
    if (source === "usaspending") return "USASpending";
    if (source === "granted_ai") return "GrantedAI";
    return "Grants.gov";
  };

  const deadlineVerb = (source) => (source === "usaspending" ? "Ends" : "Closes");

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

  function renderSkeleton(count = 3) {
    return Array.from({ length: count }, (_, i) => `
      <article class="match-card match-skeleton" style="--i: ${i}" aria-hidden="true">
        <div class="score-badge skeleton-block"></div>
        <div class="match-content">
          <div class="match-head">
            <div class="skeleton-line skeleton-title"></div>
            <div class="skeleton-pill"></div>
          </div>
          <div class="match-info">
            <div class="skeleton-line short"></div>
            <div class="skeleton-line medium"></div>
            <div class="skeleton-line short"></div>
          </div>
          <div class="match-footer">
            <div class="skeleton-line tiny"></div>
            <div class="match-actions">
              <div class="skeleton-btn"></div>
              <div class="skeleton-btn wide"></div>
            </div>
          </div>
        </div>
      </article>
    `).join("");
  }

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

    if (facts.length) {
      rows.push(`<div class="fact-grid">${facts.join("")}</div>`);
    }

    return rows.join("") || `<div class="info-row"><span class="info-value-muted">Details unavailable</span></div>`;
  }

  function renderSaveControls(match) {
    if (match.is_saved) {
      return `<span class="saved-pill">Saved</span>`;
    }
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

  function updateSavedNavCount(count) {
    const link = document.querySelector('a.dash-nav-btn[href*="saved"]');
    if (!link || count == null) return;
    const icon = link.querySelector("svg");
    link.textContent = "";
    if (icon) link.appendChild(icon);
    link.appendChild(document.createTextNode(` Saved (${count})`));
  }

  function bindSaveForms() {
    board.querySelectorAll(".inline-save-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        event.stopPropagation();

        const button = form.querySelector(".btn-save");
        if (button) {
          button.disabled = true;
          button.textContent = "Saving…";
        }

        try {
          const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: {
              Accept: "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "same-origin",
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || !data.ok) {
            throw new Error(data.error || "save_failed");
          }
          form.replaceWith(Object.assign(document.createElement("span"), {
            className: "saved-pill",
            textContent: "Saved",
          }));
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

  function renderCard(match, index) {
    const score = match.score != null && match.score !== ""
      ? Number(match.score).toFixed(2)
      : "—";
    const chance = match.chance_percent != null ? match.chance_percent : Math.round((Number(match.score) || 0) * 100);
    const collapsed = index >= 5 ? " is-collapsed" : "";
    const titleHtml = match.url
      ? `<a class="match-title-link" href="${attr(match.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(match.title || "Untitled grant")}</a>`
      : escapeHtml(match.title || "Untitled grant");

    return `
      <article class="match-card${collapsed}" style="--i: ${index}">
        <div class="score-badge" title="${chance}% chance" aria-label="Match score ${score}, ${chance} percent chance">
          ${escapeHtml(score)}
        </div>
        <div class="match-content">
          <div class="match-head">
            <h2>${titleHtml}</h2>
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

  function updateSubtitle(count, location) {
    if (!subtitle) return;
    const city = location?.city || "";
    const state = location?.state || "";
    let place = "";
    if (city || state) {
      place = `, for ${[city, state].filter(Boolean).join(", ")}`;
    }
    const noun = count === 1 ? "opportunity" : "opportunities";
    subtitle.textContent = `${count} ranked ${noun} from Grants.gov, USASpending, and GrantedAI${place}`;
  }

  function bindViewMore(hiddenCount) {
    if (hiddenCount <= 0) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "view-more-btn";
    btn.id = "view-more-matches";
    btn.textContent = `View ${hiddenCount} more matches`;
    btn.addEventListener("click", () => {
      board.querySelectorAll(".match-card.is-collapsed").forEach((el) => {
        el.classList.remove("is-collapsed");
      });
      btn.remove();
    });
    board.appendChild(btn);
  }

  function showEmpty(location) {
    const city = location?.city || "";
    const state = location?.state || "";
    const place = city || state
      ? ` and location (${[city, state].filter(Boolean).join(", ")})`
      : "";
    board.innerHTML = `
      <p class="dashboard-empty">
        No ranked matches yet for your category${escapeHtml(place)}.
        Update Settings and refresh.
      </p>
    `;
  }

  async function loadMatches() {
    board.innerHTML = `
      <div class="matches-loading-banner" role="status" aria-live="polite">
        <span class="matches-spinner" aria-hidden="true"></span>
        Finding and ranking grant matches…
      </div>
      ${renderSkeleton(3)}
    `;
    if (subtitle) {
      subtitle.textContent = "Loading ranked opportunities from Grants.gov, USASpending, and GrantedAI…";
    }

    try {
      const response = await fetch(apiUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const matches = Array.isArray(data.matches) ? data.matches : [];
      updateSubtitle(matches.length, data.location || {});

      if (!matches.length) {
        showEmpty(data.location || {});
        return;
      }

      board.innerHTML = matches.map((match, index) => renderCard(match, index)).join("");
      bindSaveForms();
      bindViewMore(Math.max(matches.length - 5, 0));
    } catch (_) {
      board.innerHTML = `
        <p class="dashboard-empty">
          We couldn’t load matches right now. Refresh the page to try again.
        </p>
      `;
      if (subtitle) {
        subtitle.textContent = "Unable to load ranked opportunities. Refresh to retry.";
      }
    }
  }

  loadMatches();
})();
