"use strict";
(() => {
  const modal = document.querySelector("#booking-modal");
  const body = modal.querySelector(".booking-body");
  const back = modal.querySelector(".back");
  const services = Dentara.content.services;
  let state;
  const reset = () => ({
    step: 1,
    service: "",
    time: "В первой половине дня",
    name: "",
    phone: "",
  });
  function rememberContact() {
    const form = body.querySelector("form");
    if (!form) return;
    state.name = form.elements.name.value;
    state.phone = form.elements.phone.value;
    state.time = form.elements.time.value;
  }
  function draw() {
    modal.querySelector("[data-booking-progress]").textContent =
      `0${Math.min(state.step, 3)} / 03`;
    back.hidden = state.step === 1 || state.step === 4;
    if (state.step === 1) {
      body.innerHTML = `<h2 id="booking-title" tabindex="-1">С чего начнём?</h2><div class="choice-grid">${services.map((service) => `<button type="button" data-choice="${service.id}" aria-pressed="${state.service === service.id}">${service.title}<span aria-hidden="true">↗</span></button>`).join("")}<button type="button" data-choice="consultation" aria-pressed="${state.service === "consultation"}">Пока нужна консультация <span aria-hidden="true">↗</span></button></div>`;
    } else if (state.step === 2) {
      body.innerHTML =
        '<h2 id="booking-title" tabindex="-1">Как вам удобно?</h2><form><label>Как к вам обращаться<input name="name" autocomplete="name" required minlength="2" maxlength="80" placeholder="Например, Анна"></label><label>Номер телефона<input name="phone" type="tel" inputmode="tel" autocomplete="tel" required maxlength="24" placeholder="+7 700 000 00 00" aria-describedby="phone-hint"></label><p id="phone-hint" class="caption">Можно использовать вымышленные данные. Они никуда не отправятся.</p><fieldset><legend>Когда удобнее связаться</legend><label class="radio-choice"><input type="radio" name="time" value="В первой половине дня">До обеда</label><label class="radio-choice"><input type="radio" name="time" value="Во второй половине дня">После обеда</label></fieldset><button class="btn" type="submit">Проверить данные <span aria-hidden="true">↗</span></button></form>';
      const form = body.querySelector("form");
      form.elements.name.value = state.name;
      form.elements.phone.value = state.phone;
      form.elements.time.value = state.time;
      form.elements.phone.addEventListener("input", () =>
        form.elements.phone.setCustomValidity(""),
      );
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const phone = form.elements.phone;
        const digits = phone.value.replace(/\D/g, "");
        phone.setCustomValidity(
          /^\+?[\d\s()-]+$/.test(phone.value) &&
            digits.length >= 10 &&
            digits.length <= 15
            ? ""
            : "Укажите телефон: от 10 до 15 цифр.",
        );
        form.elements.name.setCustomValidity(
          form.elements.name.value.trim().length >= 2
            ? ""
            : "Укажите имя: минимум 2 символа.",
        );
        if (!form.reportValidity()) return;
        rememberContact();
        state.step = 3;
        draw();
      });
      form.elements.name.addEventListener("input", () =>
        form.elements.name.setCustomValidity(""),
      );
    } else if (state.step === 3) {
      body.innerHTML =
        '<h2 id="booking-title" tabindex="-1">Всё верно?</h2><p class="caption">Это предварительный просмотр. Реальный приём не создаётся.</p><dl class="booking-summary"><dt>Направление</dt><dd data-summary="service"></dd><dt>Имя</dt><dd data-summary="name"></dd><dt>Телефон</dt><dd data-summary="phone"></dd><dt>Удобное время</dt><dd data-summary="time"></dd></dl><button class="btn" type="button" data-finish>Завершить демо <span aria-hidden="true">↗</span></button>';
      const values = {
        service:
          services.find((service) => service.id === state.service)?.title ||
          "Консультация",
        name: state.name.trim(),
        phone: state.phone,
        time: state.time,
      };
      Object.entries(values).forEach(([key, value]) => {
        body.querySelector(`[data-summary="${key}"]`).textContent = value;
      });
    } else {
      body.innerHTML =
        '<h2 id="booking-title" tabindex="-1">Первый шаг —<br><em>это просто.</em></h2><p>Демонстрация завершена. Заявка не отправлена и приём не назначен.</p><p class="demo-note">В реальной клинике на этом шаге администратор получил бы ваш запрос и согласовал время. Здесь данные будут удалены при закрытии окна.</p><button class="btn" type="button" data-done>Вернуться на сайт <span aria-hidden="true">↗</span></button>';
    }
    body.querySelector("h2").focus({ preventScroll: true });
    modal.scrollTop = 0;
  }
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-book]");
    if (!trigger) return;
    state = reset();
    const service = trigger.dataset.book;
    if (services.some((item) => item.id === service)) {
      state.service = service;
      state.step = 2;
    }
    const restore = trigger.closest("#mobile-menu")
      ? document.querySelector(".hamb")
      : trigger;
    Dentara.dialogs.open(modal, restore);
    draw();
  });
  body.addEventListener("click", (event) => {
    const choice = event.target.closest("[data-choice]");
    if (choice) {
      state.service = choice.dataset.choice;
      state.step = 2;
      draw();
    }
    if (event.target.closest("[data-finish]")) {
      state.step = 4;
      draw();
    }
    if (event.target.closest("[data-done]")) modal.close();
  });
  back.addEventListener("click", () => {
    rememberContact();
    state.step -= 1;
    draw();
  });
  modal.addEventListener("close", () => {
    if (modal.open) return;
    state = reset();
    body.replaceChildren();
  });
})();
