(() => {
  const form = document.getElementById("password-change-form");
  const button = document.getElementById("password-change-btn");
  if (!form || !button) return;

  const fields = [
    form.querySelector('[name="old_password"]'),
    form.querySelector('[name="new_password1"]'),
    form.querySelector('[name="new_password2"]'),
  ].filter(Boolean);

  function syncButton() {
    const ready = fields.every((field) => String(field.value || "").trim().length > 0);
    button.disabled = !ready;
  }

  fields.forEach((field) => {
    field.addEventListener("input", syncButton);
    field.addEventListener("change", syncButton);
  });

  form.addEventListener("submit", () => {
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = "Updating…";
  });

  syncButton();
})();
