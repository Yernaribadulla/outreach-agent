"use strict";
(() => {
  const { services, gallery } = Dentara.content;
  const list = document.querySelector(".service-list");
  const detail = document.querySelector("#service-detail");
  function selectService(id) {
    const service = services.find((item) => item.id === id) || services[0];
    list
      .querySelectorAll("button")
      .forEach((button) =>
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.service === service.id),
        ),
      );
    detail.innerHTML = `<img src="${service.image}" alt="${service.alt}" width="400" height="480" loading="lazy" decoding="async"><div class="detail-copy"><p class="eyebrow">${service.category}</p><h3>${service.title}</h3><p>${service.description}</p><div class="meta"><span><small>Продолжительность</small>${service.duration}</span><span><small>Демо стоимость</small>${service.price}</span></div><button class="inline-link" type="button" data-book="${service.id}">Обсудить на консультации <span aria-hidden="true">↗</span></button></div>`;
  }
  list.innerHTML = services
    .map(
      (service, index) =>
        `<button type="button" class="service-item" data-service="${service.id}" aria-controls="service-detail" aria-pressed="false"><span>0${index + 1}</span><strong>${service.title}</strong><i aria-hidden="true">↗</i></button>`,
    )
    .join("");
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-service]");
    if (button) selectService(button.dataset.service);
  });
  selectService(services[0].id);
  document.querySelectorAll("[data-gallery]").forEach((button) =>
    button.addEventListener("click", () => {
      const scene = gallery[Number(button.dataset.gallery)];
      const image = document.querySelector("#gallery-image");
      image.src = scene.image;
      image.alt = scene.alt;
      document.querySelector("#gallery-caption").textContent =
        `${scene.title} · иллюстративное фото`;
      document
        .querySelectorAll("[data-gallery]")
        .forEach((item) =>
          item.setAttribute("aria-pressed", String(item === button)),
        );
    }),
  );
  const toggle = document.querySelector("[data-theme-toggle]");
  const preference = matchMedia("(prefers-color-scheme: dark)");
  let explicitTheme;
  try {
    explicitTheme = localStorage.getItem("dentara-theme");
  } catch {
    /* Storage may be disabled. */
  }
  function applyTheme(theme) {
    const dark = theme === "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.setAttribute(
      "aria-label",
      dark ? "Включить светлую тему" : "Включить тёмную тему",
    );
  }
  applyTheme(explicitTheme || (preference.matches ? "dark" : "light"));
  toggle.addEventListener("click", () => {
    explicitTheme =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(explicitTheme);
    try {
      localStorage.setItem("dentara-theme", explicitTheme);
    } catch {
      /* Theme still works in memory. */
    }
  });
  preference.addEventListener("change", (event) => {
    if (!explicitTheme) applyTheme(event.matches ? "dark" : "light");
  });
  document.querySelector("[data-year]").textContent = new Date().getFullYear();
  if ("IntersectionObserver" in window) {
    const links = [...document.querySelectorAll(".nav nav a")];
    const observer = new IntersectionObserver(
      (entries) => {
        entries
          .filter((entry) => entry.isIntersecting)
          .forEach((entry) =>
            links.forEach((link) => {
              if (link.hash === `#${entry.target.id}`)
                link.setAttribute("aria-current", "location");
              else link.removeAttribute("aria-current");
            }),
          );
      },
      { rootMargin: "-15% 0px -65% 0px" },
    );
    document
      .querySelectorAll("main section[id]")
      .forEach((section) => observer.observe(section));
  }
})();
