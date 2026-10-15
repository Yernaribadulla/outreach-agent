"use strict";
(() => {
  const triggers = new WeakMap();
  const menu = document.querySelector("#mobile-menu");
  const hamburger = document.querySelector(".hamb");
  Dentara.dialogs = {
    open(dialog, trigger = document.activeElement) {
      document.querySelectorAll("dialog[open]").forEach((open) => open.close());
      triggers.set(dialog, trigger);
      dialog.showModal();
    },
    close(dialog) {
      dialog.close();
    },
  };
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog
      .querySelector("[data-close]")
      .addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (
        event.clientX < bounds.left ||
        event.clientX > bounds.right ||
        event.clientY < bounds.top ||
        event.clientY > bounds.bottom
      )
        dialog.close();
    });
    dialog.addEventListener("close", () => {
      if (dialog === menu) hamburger.setAttribute("aria-expanded", "false");
      // A new modal may have opened during a menu → booking transition.
      if (document.querySelector("dialog[open]")) return;
      const trigger = triggers.get(dialog);
      if (trigger?.isConnected && trigger.getClientRects().length)
        trigger.focus({ preventScroll: true });
    });
  });
  hamburger.addEventListener("click", () => {
    Dentara.dialogs.open(menu, hamburger);
    hamburger.setAttribute("aria-expanded", "true");
  });
  menu
    .querySelectorAll("a")
    .forEach((link) => link.addEventListener("click", () => menu.close()));
})();
