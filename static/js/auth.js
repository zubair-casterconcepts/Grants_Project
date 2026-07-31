document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".auth-form");
  if (!form) return;

  form.addEventListener("submit", () => {
    const button = form.querySelector(".btn-primary");
    if (button) {
      button.disabled = true;
      button.textContent = "Signing in…";
    }
  });
});
