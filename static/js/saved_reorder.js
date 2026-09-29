/*
 * Saved page: drag cards up/down to reorder them (grants and foundations each in
 * their own section). With a mouse, grab the card anywhere (not on its links or
 * buttons); on touch, use the ⋮⋮ handle; with the keyboard, focus the handle and
 * press Arrow Up / Arrow Down.
 * The new order is saved, so it stays after a reload.
 *
 * Smooth movement: the dragged card follows the pointer, the cards it passes
 * slide out of its way, and on release it glides into its new place.
 *
 * The dragged card itself is never taken out of the page: its neighbours are
 * moved around it instead, so the browser keeps delivering the drag's pointer
 * events (and keyboard focus stays on the handle).
 */
(() => {
  const root = document.querySelector("[data-reorder-url]");
  if (!root) return;
  const reorderUrl = root.dataset.reorderUrl;
  const csrfToken = root.dataset.csrfToken || "";
  const EDGE = 80; // px from the viewport edge where dragging auto-scrolls
  const SCROLL_SPEED = 14;
  const SLIDE_MS = 200;
  const SLIDE_EASE = "cubic-bezier(0.2, 0, 0, 1)";

  const isCard = (el) => !!el && el.classList.contains("match-card");
  const cardsOf = (board) => Array.from(board.querySelectorAll(":scope > .match-card"));
  const idsOf = (board) => cardsOf(board).map((card) => card.dataset.savedId);

  // Where a card sits in the layout (ignoring any slide/drag transform on it).
  const shiftOf = (el) => {
    const t = getComputedStyle(el).transform;
    return t && t !== "none" ? new DOMMatrixReadOnly(t).m42 : 0;
  };
  const layoutTop = (el) => el.getBoundingClientRect().top - shiftOf(el);
  const layoutMiddle = (el) => layoutTop(el) + el.offsetHeight / 2;

  // Run `mutate` (a DOM move), then slide `cards` from where they were shown to
  // their new places instead of letting them jump.
  function slide(cards, mutate) {
    const shownAt = new Map(cards.map((c) => [c, c.getBoundingClientRect().top]));
    // A card moved in the page would replay its load-in animation; it has already played.
    cards.forEach((c) => {
      c.style.animation = "none";
    });
    mutate();
    cards.forEach((c) => {
      const delta = shownAt.get(c) - layoutTop(c);
      if (Math.abs(delta) < 0.5) return;
      c.style.transition = "none";
      c.style.transform = `translateY(${delta}px)`;
      c.getBoundingClientRect(); // commit the start position
      c.style.transition = `transform ${SLIDE_MS}ms ${SLIDE_EASE}`;
      c.style.transform = "";
      clearTimeout(c._slideTimer);
      c._slideTimer = setTimeout(() => {
        if (!c.style.transform) c.style.transition = "";
      }, SLIDE_MS + 60);
    });
  }

  async function saveOrder(board, previousIds) {
    try {
      const response = await fetch(reorderUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
        body: JSON.stringify({ kind: board.dataset.reorderKind, ids: idsOf(board) }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error("reorder_failed");
    } catch (_) {
      // Put the cards back so the page matches what is saved.
      const cards = cardsOf(board);
      const byId = new Map(cards.map((card) => [card.dataset.savedId, card]));
      slide(cards, () => {
        previousIds.forEach((id) => {
          const card = byId.get(id);
          if (card) board.appendChild(card);
        });
      });
    }
  }

  // Swap with a neighbour once the dragged card's edge passes the neighbour's
  // middle (top edge going up, bottom edge going down), sliding the neighbour.
  // Using the card's edges rather than the pointer makes dragging back undo a swap.
  function followPointer(board, card, cardTop) {
    const cardBottom = cardTop + card.offsetHeight;
    const others = cardsOf(board).filter((c) => c !== card);
    slide(others, () => {
      let prev = card.previousElementSibling;
      while (isCard(prev) && cardTop < layoutMiddle(prev)) {
        card.after(prev);
        prev = card.previousElementSibling;
      }
      let next = card.nextElementSibling;
      while (isCard(next) && cardBottom > layoutMiddle(next)) {
        card.before(next);
        next = card.nextElementSibling;
      }
    });
  }

  // Pressing these must keep working as usual, so a drag never starts on them.
  const INTERACTIVE = "a, button:not(.drag-handle), input, textarea, select, label, summary";
  const DRAG_THRESHOLD = 6; // px a card-body press must move before it becomes a drag
  let sorting = false;
  let pressed = false; // a press on a card that may turn into a drag
  document.addEventListener("selectstart", (event) => {
    if (sorting) event.preventDefault();
  });
  // While a card is pressed, the browser must not start its own drag of the
  // selected text or the title link: that would cancel our drag half-way.
  document.addEventListener("dragstart", (event) => {
    if (pressed || sorting) event.preventDefault();
  });

  document.querySelectorAll(".match-board[data-reorder-kind]").forEach((board) => {
    board.addEventListener("pointerdown", (event) => {
      if (event.button > 0) return;
      const card = event.target.closest(".match-card");
      if (!card || card.parentElement !== board || cardsOf(board).length < 2) return;
      const onHandle = !!event.target.closest(".drag-handle");
      if (!onHandle) {
        // Mouse/pen can grab the card anywhere; on touch the card body scrolls the page,
        // so touch drags start from the handle.
        if (event.pointerType === "touch" || event.target.closest(INTERACTIVE)) return;
      } else {
        event.preventDefault();
      }

      pressed = true;
      const before = idsOf(board);
      const startY = event.clientY;
      // Keep the same spot of the card under the pointer while it moves.
      const grabOffset = startY - card.getBoundingClientRect().top;
      let lastY = startY;
      let frame = 0;
      let started = false;

      const placeUnderPointer = () => {
        card.style.transform = `translateY(${lastY - grabOffset - layoutTop(card)}px)`;
      };
      // Keeps scrolling while the pointer rests near the top/bottom edge.
      const autoScroll = () => {
        const step = lastY < EDGE ? -SCROLL_SPEED : lastY > window.innerHeight - EDGE ? SCROLL_SPEED : 0;
        if (step) {
          window.scrollBy(0, step);
          followPointer(board, card, lastY - grabOffset);
          placeUnderPointer();
        }
        frame = requestAnimationFrame(autoScroll);
      };
      const start = () => {
        started = true;
        sorting = true;
        window.getSelection()?.removeAllRanges();
        clearTimeout(card._slideTimer);
        card.style.animation = "none";
        board.classList.add("is-sorting");
        card.classList.add("is-dragging");
        card.style.transition = "none";
        placeUnderPointer();
        frame = requestAnimationFrame(autoScroll);
      };
      if (onHandle) start();

      const onMove = (e) => {
        if (e.pointerId !== event.pointerId) return;
        lastY = e.clientY;
        if (!started) {
          // A plain click (or a tiny wobble) on the card is not a drag.
          if (Math.abs(lastY - startY) < DRAG_THRESHOLD) return;
          start();
        }
        followPointer(board, card, lastY - grabOffset);
        placeUnderPointer();
      };
      const onEnd = (e) => {
        if (e.pointerId !== event.pointerId) return;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onEnd);
        window.removeEventListener("pointercancel", onEnd);
        pressed = false;
        if (!started) return;
        cancelAnimationFrame(frame);
        sorting = false;
        board.classList.remove("is-sorting");
        // Glide into the new place, then drop the "lifted" look.
        card.style.transition = `transform ${SLIDE_MS}ms ${SLIDE_EASE}`;
        card.style.transform = "";
        card._slideTimer = setTimeout(() => {
          card.classList.remove("is-dragging");
          card.style.transition = "";
        }, SLIDE_MS + 20);
        if (idsOf(board).join(",") !== before.join(",")) saveOrder(board, before);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onEnd);
      window.addEventListener("pointercancel", onEnd);
    });

    board.addEventListener("keydown", (event) => {
      const handle = event.target.closest(".drag-handle");
      if (!handle || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
      event.preventDefault();
      const card = handle.closest(".match-card");
      const neighbour = event.key === "ArrowUp" ? card.previousElementSibling : card.nextElementSibling;
      if (!isCard(neighbour)) return;
      const before = idsOf(board);
      slide([card, neighbour], () => {
        if (event.key === "ArrowUp") card.after(neighbour);
        else card.before(neighbour);
      });
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
      saveOrder(board, before);
    });
  });
})();
