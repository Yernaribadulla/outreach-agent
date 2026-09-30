const services = [
  [
    "01",
    "Имплантация",
    "Хирургия",
    "Восстанавливаем эстетику и функцию с опорой на цифровую диагностику.",
    "от 3 месяцев",
    "от 185 000 ₸",
  ],
  [
    "02",
    "Ортодонтия",
    "Эстетика",
    "Незаметное выравнивание прикуса и создание уверенной улыбки.",
    "от 6 месяцев",
    "от 42 000 ₸ / мес",
  ],
  [
    "03",
    "Дизайн улыбки",
    "Digital lab",
    "Цифровое моделирование будущего результата до начала лечения.",
    "1–2 визита",
    "от 35 000 ₸",
  ],
  [
    "04",
    "Профилактика",
    "Care",
    "Профессиональная гигиена и спокойное наблюдение без лишнего лечения.",
    "60 минут",
    "от 28 000 ₸",
  ],
];
// Direction records are intentionally separate from future specialist profiles.
// Profile fields are nullable until verified source material is available.
const doctors = [
  {
    direction: "ОРТОДОНТИЯ",
    title: "Планирование прикуса",
    summary: "Цифровая диагностика и последовательное ортодонтическое лечение.",
    items: [
      "Планирование прикуса",
      "Цифровая диагностика",
      "Ортодонтическое лечение",
      "Индивидуальный план лечения",
    ],
    name: null,
    specialty: null,
    experience: null,
    education: null,
    achievements: null,
    photo: null,
    bioSource: null,
    photoSource: null,
    photoLicense: null,
    art: "portrait-one",
    mark: "01",
  },
  {
    direction: "ХИРУРГИЯ",
    title: "Хирургическое сопровождение",
    summary:
      "Подготовка, имплантация и восстановление в понятном маршруте лечения.",
    items: [
      "Имплантация",
      "Хирургическое сопровождение",
      "Подготовка к имплантации",
      "Восстановление",
    ],
    name: null,
    specialty: null,
    experience: null,
    education: null,
    achievements: null,
    photo: null,
    bioSource: null,
    photoSource: null,
    photoLicense: null,
    art: "portrait-two",
    mark: "02",
  },
  {
    direction: "ЭСТЕТИКА",
    title: "Цифровая улыбка",
    summary:
      "Дизайн улыбки и визуализация результата до начала эстетической реставрации.",
    items: [
      "Дизайн улыбки",
      "Цифровое планирование",
      "Эстетическая реставрация",
      "Визуализация результата",
    ],
    name: null,
    specialty: null,
    experience: null,
    education: null,
    achievements: null,
    photo: null,
    bioSource: null,
    photoSource: null,
    photoLicense: null,
    art: "portrait-three",
    mark: "03",
  },
];
const faqAnswers = [
  "На первой встрече врач выслушивает ваши жалобы и пожелания, проводит осмотр и при необходимости назначает диагностику. После этого мы обсуждаем возможные варианты лечения, сроки и ориентировочную стоимость. Вы получаете понятный план следующих шагов без обязательства начинать лечение сразу.",
  "Срок зависит от задачи и выбранного метода. Например, профессиональная гигиена обычно занимает около часа, а ортодонтическое лечение может продолжаться несколько месяцев. После диагностики врач составит индивидуальный план и обозначит реалистичные сроки для вашего случая.",
  "Да. Если появилась острая боль, отёк, скол или другая проблема, мы постараемся подобрать ближайшее доступное время для осмотра. На экстренной консультации врач сначала определит причину состояния и предложит способ безопасно снять острые симптомы.",
  "Мы принимаем оплату картой и наличными, а для длительных программ можем обсудить удобный график платежей. Финальные условия зависят от выбранного плана лечения.",
  "Да, принимаем детей с раннего возраста. Первый визит проходит в спокойном формате знакомства, без давления и обязательных процедур.",
];
const $ = (s) => document.querySelector(s),
  $$ = (s) => document.querySelectorAll(s);
function renderServices() {
  $(".service-list").innerHTML = services
    .map(
      (x, i) =>
        `<button class="service-item ${i ? "" : "active"}" data-service="${i}"><span>${x[0]}</span><b>${x[1]}</b><i><svg class="icon-arrow" aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M5 19 19 5M8 5h11v11"/></svg></i></button>`,
    )
    .join("");
  $$(".service-item").forEach(
    (b) =>
      (b.onclick = () => {
        let next = +b.dataset.service,
          old = +$(".service-item.active").dataset.service;
        if (next === old) return;
        $$(".service-item").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        let x = services[next],
          detail = $(".service-detail");
        detail.classList.remove("is-switching");
        void detail.offsetWidth;
        detail.classList.add(
          next > old ? "direction-next" : "direction-prev",
          "is-switching",
        );
        setTimeout(() => detail.classList.remove("is-switching"), 520);
        $(".service-visual span").textContent = x[2] + " / DENTARA";
        $(".detail-copy .eyebrow").textContent = x[2] + " / DENTARA";
        $(".detail-copy h3").textContent = x[1];
        $(".detail-copy>p:not(.eyebrow)").textContent = x[3];
        $(".meta").innerHTML =
          `<span><small>Продолжительность</small>${x[4]}</span><span><small>Стоимость</small>${x[5]}</span>`;
      }),
  );
}
function renderDoctors() {
  const grid = $(".doctor-grid");
  if (!grid) return;
  const arrow =
    '<svg class="icon-arrow" aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M5 19 19 5M8 5h11v11"/></svg>';
  grid.innerHTML = doctors
    .map(
      (d, i) =>
        `<button class="doctor-card direction-card ${d.art}" type="button" data-doctor="${i}" aria-haspopup="dialog" aria-label="Открыть направление: ${d.direction}"><span class="doctor-avatar"><span aria-hidden="true">${d.mark}</span><i aria-hidden="true">${arrow}</i></span><span class="doctor-info"><small>${d.direction}</small><strong>${d.title}</strong><span>${d.summary}</span><em>Открыть направление <b aria-hidden="true">${arrow}</b></em></span></button>`,
    )
    .join("");
  $$(".doctor-card").forEach((b) =>
    b.addEventListener("click", () => openDoctor(+b.dataset.doctor)),
  );
}
let lastModalTrigger = null,
  lockedScrollY = 0;
function openModal(modal, trigger) {
  lastModalTrigger = trigger || document.activeElement;
  lockedScrollY = window.scrollY;
  document.body.classList.add("modal-open");
  modal.classList.add("open");
}
function closeModal(modal) {
  modal.classList.remove("open");
  document.body.classList.remove("modal-open");
  $$(".doctor-card").forEach((x) => x.classList.remove("selected"));
  lastModalTrigger?.focus({ preventScroll: true });
  lastModalTrigger = null;
}
function openDoctor(i) {
  let d = doctors[i],
    m = $("#doctor-modal"),
    trigger = document.activeElement;
  $$(".doctor-card").forEach((x) => x.classList.remove("selected"));
  m.querySelector(".modal-avatar").className = `modal-avatar ${d.art}`;
  m.querySelector(".modal-avatar").textContent = d.mark;
  m.querySelector(".eyebrow").textContent = d.direction + " / НАПРАВЛЕНИЕ";
  m.querySelector("h2").textContent = d.title;
  m.querySelector(".bio").innerHTML =
    `${d.summary}<ul>${d.items.map((item) => `<li>${item}</li>`).join("")}</ul>`;
  m.querySelector(".facts span").innerHTML =
    "<small>Фокус направления</small>" + d.direction.toLowerCase();
  openModal(m, trigger);
  m.querySelector(".x").focus({ preventScroll: true });
}
function renderJourney() {
  let names = [
    "Консультация",
    "Диагностика",
    "План лечения",
    "Процедура",
    "Восстановление",
  ];
  $(".journey-grid").innerHTML = names
    .map(
      (n, i) =>
        `<div class="journey-step reveal ${i ? "" : "active"}" style="--delay:${i * 80}ms"><span>0${i + 1}</span><b>${n}</b><small>${["Встречаемся и слушаем", "Видим полную картину", "Согласуем маршрут", "Действуем точно", "Поддерживаем результат"][i]}</small></div>`,
    )
    .join("");
}
function renderFaq() {
  let qs = [
    "Как проходит первая консультация?",
    "Сколько длится лечение?",
    "Можно ли прийти на экстренную консультацию?",
    "Какие способы оплаты доступны?",
    "Работаете ли вы с детьми?",
  ];
  $(".faq-list").innerHTML = qs
    .map(
      (q, i) =>
        `<div class="faq-item reveal" style="--delay:${i * 70}ms"><button class="faq-q" aria-expanded="false"><span>0${i + 1}</span><b>${q}</b><i>+</i></button><div class="faq-a"><p>${faqAnswers[i]}</p></div></div>`,
    )
    .join("");
  $$(".faq-q").forEach(
    (b) =>
      (b.onclick = () => {
        let item = b.parentElement,
          open = item.classList.toggle("open");
        b.setAttribute("aria-expanded", open);
        b.querySelector("i").textContent = open ? "−" : "+";
      }),
  );
}
function booking() {
  let modal = $("#booking-modal"),
    body = modal.querySelector(".booking-body"),
    step = 1;
  function draw(dir = 1) {
    modal.querySelector(".booking-top b").innerHTML = `<em>0${step}</em> / 03`;
    body.classList.remove("step-enter");
    body.style.setProperty("--dir", dir);
    void body.offsetWidth;
    body.classList.add("step-enter");
    body.innerHTML =
      step === 1
        ? `<h2 id="booking-title">Что вас<br><em>интересует?</em></h2><div class="choice-grid">${services
            .slice(0, 3)
            .map(
              (x) =>
                `<button>${x[1]} <span><svg class="icon-arrow" aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M5 19 19 5M8 5h11v11"/></svg></span></button>`,
            )
            .join("")}</div>`
        : step === 2
          ? `<h2 id="booking-title">Выберите<br><em>направление.</em></h2><div class="choice-grid">${doctors.map((x) => `<button>${x.direction} <span><svg class="icon-arrow" aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M5 19 19 5M8 5h11v11"/></svg></span></button>`).join("")}</div>`
          : `<h2 id="booking-title">Почти<br><em>готово.</em></h2><label class="field">Ваше имя<input placeholder="Имя и фамилия"></label><label class="field">Телефон<input placeholder="+7 700 000 00 00"></label><button class="btn confirm">Подтвердить запись <svg class="icon-arrow" aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M5 19 19 5M8 5h11v11"/></svg></button>`;
    body.querySelectorAll(".choice-grid button").forEach(
      (x) =>
        (x.onclick = () => {
          step++;
          draw(1);
        }),
    );
    body.querySelector(".confirm")?.addEventListener("click", () => {
      closeModal(modal);
      toast("Заявка принята — мы свяжемся с вами в течение 15 минут.");
    });
    modal.querySelector(".back").style.display = step > 1 ? "block" : "none";
  }
  draw();
  openModal(modal, document.activeElement);
  modal.querySelector(".back").onclick = () => {
    if (step > 1) {
      step--;
      draw(-1);
    }
  };
}
function toast(t) {
  let x = $(".toast");
  x.textContent = "✓  " + t;
  x.classList.add("show");
  setTimeout(() => x.classList.remove("show"), 2800);
}
renderServices();
renderDoctors();
renderJourney();
renderFaq();
$$("[data-book]").forEach((b) => (b.onclick = booking));
$$(".x").forEach(
  (b) => (b.onclick = () => closeModal(b.closest(".modal-layer"))),
);
$("#doctor-modal [data-book]").onclick = () => {
  closeModal($("#doctor-modal"));
  booking();
};
$$(".modal-layer").forEach((layer) =>
  layer.addEventListener("click", (e) => {
    if (e.target === layer) closeModal(layer);
  }),
);
window.addEventListener(
  "scroll",
  () => $(".nav").classList.toggle("scrolled", scrollY > 40),
  { passive: true },
);
$(".hamb").onclick = () => $(".mobile-menu").classList.add("open");
$(".close-menu").onclick = () => $(".mobile-menu").classList.remove("open");
$$(".mobile-menu a").forEach(
  (a) => (a.onclick = () => $(".mobile-menu").classList.remove("open")),
);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $$(".modal-layer.open").forEach(closeModal);
});
let compare = $(".compare"),
  range = compare.querySelector("input"),
  before = compare.querySelector(".before"),
  handle = compare.querySelector(".handle"),
  target = 52,
  current = 52,
  dragging = false,
  dragMoved = false;
function sliderFrame() {
  current = target;
  before.style.clipPath = `inset(0 ${100 - current}% 0 0)`;
  handle.style.left = current + "%";
  handle.style.setProperty("--active", dragging ? "1" : "0");
}
function setCompareTarget(value) {
  target = Math.max(0, Math.min(100, value));
  range.value = target;
  sliderFrame();
}
function setCompareFromPointer(e) {
  const rect = compare.getBoundingClientRect();
  setCompareTarget(((e.clientX - rect.left) / rect.width) * 100);
}
range.oninput = () => {
  dragging = true;
  compare.classList.add("is-dragging");
  setCompareTarget(+range.value);
};
compare.addEventListener("pointerdown", (e) => {
  dragging = true;
  dragMoved = false;
  compare.classList.add("is-dragging");
  compare.setPointerCapture?.(e.pointerId);
  setCompareFromPointer(e);
  e.preventDefault();
});
compare.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  dragMoved = true;
  setCompareFromPointer(e);
  e.preventDefault();
});
function endComparePointer(e) {
  if (compare.hasPointerCapture?.(e.pointerId))
    compare.releasePointerCapture(e.pointerId);
  dragging = false;
  compare.classList.remove("is-dragging");
  sliderFrame();
}
compare.addEventListener("pointerup", endComparePointer);
compare.addEventListener("pointercancel", endComparePointer);
compare.addEventListener("click", (e) => {
  if (dragMoved) {
    dragMoved = false;
    return;
  }
  if (e.target === range || e.target === handle) return;
  setCompareFromPointer(e);
  dragging = false;
});
let modes = ["Natural", "Whitening", "Veneers", "Alignment", "Hollywood"];
$(".tabs").innerHTML = modes.map((x) => `<button>${x}</button>`).join("");
$$(".tabs button").forEach(
  (b) =>
    (b.onclick = () => {
      $$(".tabs button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      $(".smile-preview").style.setProperty(
        "--smile-bg",
        {
          Natural: "#d8c4b5",
          Whitening: "#c4d3c5",
          Veneers: "#d3cad9",
          Alignment: "#c2ced7",
          Hollywood: "#dbc6a2",
        }[b.textContent],
      );
      $(".smile-preview").classList.remove("smile-change");
      void $(".smile-preview").offsetWidth;
      $(".smile-preview").classList.add("smile-change");
    }),
);
$(".tabs button").classList.add("active");
const observer = new IntersectionObserver(
  (es) =>
    es.forEach((e) => {
      if (e.isIntersecting) {
        e.target.classList.add("seen");
        if (e.target.dataset.count) {
          let n = +e.target.dataset.count,
            i = 0,
            t = setInterval(() => {
              i += Math.ceil(n / 28);
              if (i >= n) {
                i = n;
                clearInterval(t);
              }
              e.target.firstChild.textContent = i;
            }, 30);
        }
      }
    }),
  { threshold: 0.16 },
);
$$(".reveal,[data-count]").forEach((x) => observer.observe(x));
const galleryScenes = [
  ["Пространство клиники", "assets/clinic-space.jpg"],
  ["Консультация", "assets/hero-clinic.jpg"],
  ["Цифровая лаборатория", "assets/dental-detail.webp"],
  ["Зона восстановления", "assets/clinic-space.jpg"],
];
$$("[data-gallery]").forEach(
  (b) =>
    (b.onclick = () => {
      let i = +b.dataset.gallery,
        m = $("[data-gallery-main]");
      $$("[data-gallery]").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      m.classList.add("is-changing");
      setTimeout(() => {
        m.style.backgroundImage = `linear-gradient(180deg,#17251c22,#17251c99),url("${galleryScenes[i][1]}")`;
        m.querySelector("span").textContent = galleryScenes[i][0];
        m.classList.remove("is-changing");
      }, 220);
    }),
);

// Premium theme and section-aware navigation.
const dentaraThemeKey = "dentara-theme";
function applyDentaraTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(dentaraThemeKey, theme);
  const toggle = document.querySelector("[data-theme-toggle]");
  if (toggle)
    toggle.textContent = theme === "dark" ? "Светлая тема" : "Тёмная тема";
}
const savedDentaraTheme = localStorage.getItem(dentaraThemeKey);
applyDentaraTheme(
  savedDentaraTheme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
);
const themeButton = document.querySelector("[data-theme-toggle]");
themeButton?.addEventListener("click", () =>
  applyDentaraTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
  ),
);
const observedSections = [...document.querySelectorAll("main section[id]")];
const navLinks = [
  ...document.querySelectorAll(".nav nav a,.mobile-menu nav a"),
];
const spy = new IntersectionObserver(
  (entries) =>
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        navLinks.forEach((link) =>
          link.classList.toggle(
            "active",
            link.getAttribute("href") === "#" + entry.target.id,
          ),
        );
      }
    }),
  { rootMargin: "-25% 0px -60% 0px", threshold: 0 },
);
observedSections.forEach((section) => spy.observe(section));
navLinks.forEach((link) =>
  link.addEventListener("click", (event) => {
    const target = document.querySelector(link.getAttribute("href"));
    if (target) {
      event.preventDefault();
      target.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
      document.querySelector(".mobile-menu")?.classList.remove("open");
    }
  }),
);
