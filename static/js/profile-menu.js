(() => {
  const menus = document.querySelectorAll("[data-profile-menu]");
  if (!menus.length) return;

  function closeMenu(menu) {
    const trigger = menu.querySelector(".profile-menu-trigger");
    const dropdown = menu.querySelector(".profile-menu-dropdown");
    if (!trigger || !dropdown) return;
    menu.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
    dropdown.setAttribute("hidden", "");
  }

  function openMenu(menu) {
    menus.forEach((other) => {
      if (other !== menu) closeMenu(other);
    });
    const trigger = menu.querySelector(".profile-menu-trigger");
    const dropdown = menu.querySelector(".profile-menu-dropdown");
    if (!trigger || !dropdown) return;
    menu.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    dropdown.removeAttribute("hidden");
  }

  menus.forEach((menu) => {
    const trigger = menu.querySelector(".profile-menu-trigger");
    if (!trigger) return;

    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (menu.classList.contains("is-open")) {
        closeMenu(menu);
      } else {
        openMenu(menu);
      }
    });
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    menus.forEach((menu) => {
      if (!menu.contains(target)) {
        closeMenu(menu);
      }
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      menus.forEach(closeMenu);
    }
  });
})();
