// Edit / delete controls on the Feedback reasons page.
(() => {
  const dialog = document.getElementById("fr-delete-dialog");
  const dialogCopy = document.getElementById("fr-delete-copy");
  const REASON_MESSAGE = "Please add a short reason — a few words is enough.";
  let pendingDelete = null;

  // minlength counts spaces; block whitespace-only reasons before the form
  // submits, so the page loader never starts for a submit that can't succeed.
  function checkReason(textarea) {
    const min = Number(textarea.getAttribute("minlength")) || 3;
    textarea.setCustomValidity(textarea.value.trim().length < min ? REASON_MESSAGE : "");
  }

  function setEditOpen(form, open) {
    const toggle = document.querySelector(`.fr-edit-toggle[aria-controls="${form.id}"]`);
    form.hidden = !open;
    if (toggle) {
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "Close" : "Edit";
    }
    if (!open) return;
    const textarea = form.querySelector("textarea");
    if (!textarea) return;
    checkReason(textarea);
    textarea.focus();
    const end = textarea.value.length;
    textarea.setSelectionRange(end, end);
  }

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest(".fr-edit-toggle");
    if (toggle) {
      const form = document.getElementById(toggle.getAttribute("aria-controls") || "");
      if (form) setEditOpen(form, form.hidden);
      return;
    }
    const cancel = event.target.closest(".fr-edit-cancel");
    if (cancel) {
      const form = cancel.closest(".fr-edit");
      if (!form) return;
      form.reset(); // back to the saved verdict and reason
      setEditOpen(form, false);
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.matches(".fr-edit textarea")) checkReason(event.target);
  });

  function submitDelete(form) {
    form.dataset.confirmed = "true";
    // Delete forms opt out of the automatic loader (it would start before the
    // confirmation and stay up on Cancel); show it only once confirmed.
    if (window.GrantsPageLoader) window.GrantsPageLoader.show("Deleting feedback…");
    form.submit();
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.classList.contains("fr-delete-form")) return;
    if (form.dataset.confirmed === "true") return;
    event.preventDefault();
    const message = form.dataset.confirm || "Delete this feedback?";
    if (!dialog || typeof dialog.showModal !== "function") {
      if (window.confirm(message)) submitDelete(form);
      return;
    }
    pendingDelete = form;
    if (dialogCopy) dialogCopy.textContent = message;
    dialog.returnValue = "";
    dialog.showModal();
  });

  if (dialog) {
    // Cancel, Escape and Delete all close the dialog; only Delete submits.
    dialog.addEventListener("close", () => {
      const form = pendingDelete;
      pendingDelete = null;
      if (form && dialog.returnValue === "delete") submitDelete(form);
    });
  }
})();
