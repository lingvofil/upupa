"use strict";
const main = document.getElementById("stage");
const dialog = document.getElementById("modal");
const modalBody = document.getElementById("modal-body");
const notice = document.getElementById("notice");
const asset = (name) => "?view=guru&asset=" + encodeURIComponent(name);
const portraitFile = (id) =>
  id === "jealous.vocal"
    ? "base.webp"
    : "guest-" + id.replace(".", "-") + ".jpg";
const labels = {
  questions: "Знакомство",
  spread: "Ваш расклад",
  tarot: "Таро",
  palm: "Ладонь",
  word: "Ладонь",
  horoscope: "Гороскоп",
  forecast: "Гороскоп",
  tone: "Предсказание",
  reaction: "После ваших слов",
  final: "Напутствие",
};
let tg,
  state,
  record,
  page,
  busy = false,
  noticeTimer;

function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function button(text, action, cls) {
  const el = node("button", text, cls);
  el.onclick = () => run(action);
  return el;
}
function img(name, alt, cls) {
  const el = node("img", "", cls);
  el.src = asset(name);
  el.alt = alt;
  return el;
}
function group(...items) {
  const el = node("div", "", "actions");
  el.append(...items);
  return el;
}
function say(text) {
  clearTimeout(noticeTimer);
  notice.textContent = text;
  notice.hidden = false;
  noticeTimer = setTimeout(() => {
    notice.hidden = true;
  }, 6500);
}
async function run(action) {
  if (busy) return;
  busy = true;
  main.setAttribute("aria-busy", "true");
  try {
    await action();
  } catch (err) {
    if (err.status === 409) {
      try {
        state = await call("home");
        state.current ? session() : salon();
      } catch {
        /* show original error */
      }
    }
    say(err.message || "Не удалось открыть салон. Попробуйте ещё раз.");
  } finally {
    busy = false;
    main.removeAttribute("aria-busy");
  }
}
async function call(op, fields = {}) {
  const res = await fetch("?view=guru", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": tg?.initData || "",
    },
    body: JSON.stringify({ op, rev: state?.rev, ...fields }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const err = new Error(
      data.error ||
        (res.status === 401
          ? "Откройте игру кнопкой «Открыть дверь» в Telegram."
          : "Не удалось получить ответ салона. Повторите ход."),
    );
    err.status = res.status;
    throw err;
  }
  return res.json();
}
function mount(name, el) {
  if (dialog.open) dialog.close();
  page = name;
  main.replaceChildren(el);
  window.scrollTo(0, 0);
  if (name === "door") tg?.BackButton?.hide();
  else tg?.BackButton?.show();
}
function panel(title) {
  const el = node("section", "", "page");
  el.append(node("h1", title));
  return el;
}
function modal(title, ...children) {
  modalBody.replaceChildren(node("h2", title), ...children);
  if (!dialog.open) dialog.showModal();
}
function door() {
  const el = node("section", "", "hero"),
    content = node("div", "", "hero-copy");
  const active = state?.current;
  content.append(
    node("h1", active ? "Вас ждут." : "В дверь постучали."),
    node(
      "p",
      active
        ? active.client + " ждёт за столом. Ваш разговор сохранён."
        : "За порогом — посетитель. Зажгите лампу и узнайте, с чем он пришёл.",
    ),
    group(
      button(
        active ? "Продолжить приём →" : "Открыть дверь →",
        () => start(),
        "primary",
      ),
    ),
  );
  el.append(content);
  mount("door", el);
}
async function start(story) {
  if (!state) state = await call("home");
  if (!state.current) state = await call("start", story ? { story } : {});
  session();
}
function sceneArt(s) {
  const art = node("aside", "", "stage-art");
  if (s.phase === "spread") {
    const spread = node("div", "", "card-backs");
    for (const a of s.actions) {
      const tile = button("✴", () => move(a.id), "card-back");
      tile.setAttribute("aria-label", "Перевернуть · " + a.label);
      tile.title = a.label;
      spread.append(tile);
    }
    art.append(spread);
  } else if (s.art) {
    const zoom = button(
      "",
      () =>
        modal(
          "Карта крупным планом",
          img("card-" + s.art + ".jpg", "Карта на столе", "zoom-art"),
        ),
      "zoom",
    );
    zoom.setAttribute("aria-label", "Рассмотреть рисунок карты");
    zoom.append(img("card-" + s.art + ".jpg", "Карта на столе", "arcana"));
    art.append(zoom);
  } else if (["palm", "word"].includes(s.phase))
    art.append(img("palm.webp", "Расположение линий ладони", "instrument"));
  else if (
    ["horoscope", "forecast"].includes(s.phase) ||
    (s.phase === "tone" && s.method === "horoscope")
  ) {
    const wheel = node("div", "☽", "wheel");
    const planets = s.actions.filter((a) => a.kind === "sign");
    planets.forEach((a, i) => {
      const angle = (i * 2 * Math.PI) / planets.length - Math.PI / 2,
        planet = button(a.label.split(" ")[0], () => move(a.id), "planet");
      planet.setAttribute("aria-label", "Рассмотреть · " + a.label);
      planet.style.left = 50 + 48 * Math.cos(angle) + "%";
      planet.style.top = 50 + 48 * Math.sin(angle) + "%";
      wheel.append(planet);
    });
    art.append(wheel);
  } else if (s.story === "jealous.vocal") {
    const portrait =
      { angry: "irritated", hopeful: "relieved", tired: "suspicious" }[
        s.mood
      ] || "base";
    art.append(img(portrait + ".webp", s.client, "portrait"));
  } else art.append(img(portraitFile(s.story), s.client, "cameo"));
  art.append(node("span", s.client, "plate"));
  return art;
}
async function move(id) {
  state = await call("move", { action: id });
  if (state.record) showRecord(state.record);
  else session();
}
function session() {
  const s = state.current;
  if (!s) return salon();
  const el = node("section", "", "session"),
    text = node("article", "", "paper");
  text.append(
    node(
      "div",
      s.client + " · " + s.finishedMethods.length + " из 3 практик",
      "meta",
    ),
    node("h2", labels[s.phase]),
  );
  const speech = node("div", "", "speech");
  for (const line of s.lines)
    speech.append(node("p", line.replace(/^🚪\s*/u, "")));
  text.append(speech);
  if (s.screen) text.append(node("p", s.screen, "screen"));
  text.append(node("div", s.prompt, "prompt"));
  const choices = node("div", "", "actions");
  const rest = node("details"),
    secondary = node("div", "", "actions");
  rest.append(node("summary", "Другие вопросы и практики"), secondary);
  for (const a of s.actions) {
    const container =
      s.opening.length && !s.opening.includes(a.id) ? secondary : choices;
    container.append(
      button(
        a.label,
        () => move(a.id),
        a.kind === "method" ? "method" : a.id === "scene" ? "primary" : "",
      ),
    );
  }
  const journal = node("details");
  journal.append(
    node(
      "summary",
      s.opening.length ? "Выслушать весь рассказ" : "Что прозвучало",
    ),
    ...s.heard.map((line) => node("p", line)),
  );
  const pause = button(
    "Выйти из приёма",
    () =>
      modal(
        "Закрыть дверь?",
        node(
          "p",
          "Можно оставить встречу на паузе или проводить гостя без предсказания.",
        ),
        group(
          button("Сохранить и выйти", salon, "primary"),
          button("Проводить гостя", async () => {
            state = await call("leave");
            salon();
          }),
        ),
      ),
    "quiet back",
  );
  text.append(choices);
  if (secondary.childElementCount) text.append(rest);
  text.append(journal, pause);
  el.append(sceneArt(s), text);
  mount("session", el);
}
function ticket(title, detail, action, picture) {
  const el = button("", action, "ticket"),
    copy = node("span");
  if (picture) el.append(img(picture, ""));
  copy.append(node("small", detail), node("strong", title));
  el.append(copy);
  return el;
}
function salon() {
  if (!state) return door();
  const el = panel("Мой салон");
  el.append(
    node(
      "p",
      state.history.length
        ? "Приёмов в журнале: " +
            state.history.length +
            " · Касса: " +
            state.history
              .reduce((sum, r) => sum + r.fee, 0)
              .toLocaleString("ru") +
            " ₽"
        : "Здесь будут письма посетителей и записи о ваших приёмах.",
      "muted",
    ),
  );
  el.append(
    group(
      button(
        state.current
          ? "Продолжить · " + state.current.client
          : "Принять посетителя",
        () => start(),
        "primary",
      ),
      button("Книга посетителей · " + state.count, guests),
    ),
  );
  const letters = state.history.filter((r) => r.waiting);
  if (letters.length) {
    const box = node("section");
    box.append(node("h2", "Письма у двери"));
    const list = node("div", "", "choice-list");
    for (const r of letters)
      list.append(
        ticket(r.client, "Письмо после встречи", async () =>
          letter((await call("record", { id: r.id })).record),
        ),
      );
    box.append(list);
    el.append(box);
  }
  if (state.history.length) {
    const box = node("section");
    box.append(node("h2", "Журнал приёмов"));
    const list = node("div", "", "choice-list");
    for (const r of state.history)
      list.append(
        ticket(
          r.title,
          r.client + (r.keepsake ? " · " + r.keepsake : ""),
          async () => showRecord((await call("record", { id: r.id })).record),
        ),
      );
    box.append(list);
    el.append(box);
  }
  mount("salon", el);
}
async function guests() {
  const { catalog } = await call("catalog"),
    el = panel("Книга посетителей");
  if (state.current)
    el.append(button("Продолжить текущий приём", session, "primary"));
  const grid = node("div", "", "grid");
  for (const g of catalog) {
    const guest = ticket(g.name, g.line, () => start(g.id), portraitFile(g.id));
    guest.disabled = Boolean(state.current);
    grid.append(guest);
  }
  el.append(grid);
  mount("guests", el);
}
function showRecord(r) {
  record = r;
  const el = panel(r.title),
    body = node("article", "", "paper record");
  if (r.story === "jealous.vocal" && r.ending === "duet")
    body.append(img("ending-duet.webp", "Финал в ресторане", "arcana"));
  body.append(
    node("div", r.client + " · " + r.fee + " ₽", "meta"),
    node("p", r.text),
    node("p", r.quote),
    node("p", r.afterword),
  );
  const breakdown = node("details");
  breakdown.append(
    node("summary", "Что сбылось и почему"),
    node("p", r.epilogue),
  );
  body.append(breakdown);
  if (r.keepsake) body.append(node("p", "На память · " + r.keepsake));
  el.append(body);
  if (r.cards.length) {
    el.append(node("h2", "Карта для чата"));
    const list = node("div", "", "grid");
    r.cards.forEach((c, slot) =>
      list.append(
        ticket(
          c.title,
          c.symbol,
          () => compose(r.id, slot, c),
          "card-" + c.art + ".jpg",
        ),
      ),
    );
    el.append(list);
  }
  el.append(group(button("В мой салон →", salon, "primary")));
  if (r.reply) el.append(button("Перечитать письмо", () => letter(r)));
  mount("record", el);
}
function letter(r) {
  record = r;
  const el = panel("Снова на пороге"),
    body = node("article", "", "paper letter");
  body.append(
    node("div", "После приёма «" + r.title + "»", "meta"),
    node("p", r.memory),
    node("p", "«" + r.letter + "»", "speech"),
    node("p", r.client, "signed"),
  );
  if (r.spoken)
    body.append(node("p", "На прошлом приёме прозвучало: «" + r.spoken + "»."));
  if (r.reply) body.append(...r.reply.map((t) => node("p", t)));
  else {
    const list = node("div", "", "choice-list");
    for (const [choice, label] of Object.entries(state.replies))
      list.append(
        button(label, async () => {
          state = await call("reply", { id: r.id, choice });
          letter(state.record);
        }),
      );
    body.append(list);
  }
  el.append(body, button("Вернуться в салон", salon, "back"));
  mount("letter", el);
}
function compose(id, slot, c) {
  modal(
    c.title,
    img("card-" + c.art + ".jpg", c.title, "arcana"),
    node("p", c.symbol + " · " + c.look),
    node("p", "«" + c.phrase + "»"),
    button(
      "Поделиться картой",
      async () => {
        const shared = await call("share", { id, slot });
        await send(shared.token);
      },
      "primary",
    ),
  );
}
async function send(token, option) {
  const native =
    tg?.isVersionAtLeast?.("8.0") && typeof tg.shareMessage === "function";
  const result = await call("prepare", {
    token,
    option,
    native: Boolean(native),
  });
  const url = new URL(result.url, location.href).href,
    c = result.card;
  const text =
    "🃏 " +
    c.title +
    " · " +
    c.symbol +
    "\n«" +
    c.phrase +
    "». А что здесь видишь ты?";
  if (tg?.initData) {
    const sendLink = () =>
      tg.openTelegramLink(
        "https://t.me/share/url?" + new URLSearchParams({ url, text }),
      );
    if (result.prepared) {
      tg.shareMessage(result.prepared, (sent) => {
        if (sent) say("Карта отправлена.");
        else
          modal(
            c.title,
            node("p", text),
            button("Поделиться ссылкой", sendLink),
          );
      });
    } else sendLink();
    return;
  }
  const link = node("a", "Открыть карточку");
  link.href = url;
  link.style.color = "var(--gold)";
  modal(
    "Ссылка на карту",
    node("p", text),
    link,
    button("Скопировать ссылку", async () => {
      await navigator.clipboard.writeText(url);
      say("Ссылка скопирована.");
    }),
  );
}
async function shared(token, sourceIndex) {
  const { card: c } = await call("card", { token });
  const el = panel(c.title),
    spread = node("article", "", "paper shared-card"),
    copy = node("div");
  copy.append(
    node("h2", "Что вы видите?"),
    node("p", "Выберите деталь рисунка и дайте ей своё прочтение."),
  );
  const options = node("div", "", "choice-list");
  const original = c.variants[sourceIndex] || c;
  function symbols() {
    options.replaceChildren();
    for (const detail of new Set(c.variants.map((v) => v.symbol)))
      options.append(
        button(detail, () => {
          const items = c.variants
            .map((v, i) => ({ ...v, index: i }))
            .filter((v) => v.symbol === detail);
          options.replaceChildren(
            node("p", items[0].look),
            ...items.map((v) =>
              button(v.phrase, () => {
                options.replaceChildren(
                  node("p", "Вы · " + detail + "\n«" + v.phrase + "»."),
                  node(
                    "p",
                    "В присланной карте · " +
                      original.symbol +
                      "\n«" +
                      original.phrase +
                      "».",
                  ),
                  button(
                    "Отправить мою версию",
                    () => send(token, v.index),
                    "primary",
                  ),
                  button("Рассмотреть снова", symbols),
                );
              }),
            ),
            button("Другая деталь", symbols),
          );
        }),
      );
  }
  symbols();
  copy.append(options);
  spread.append(img("card-" + c.art + ".jpg", c.title, "arcana"), copy);
  el.append(spread, button("Открыть свой салон →", salon, "primary"));
  mount("shared", el);
}
function back() {
  if (dialog.open) dialog.close();
  else if (["record", "guests", "letter", "session", "shared"].includes(page))
    salon();
  else door();
}
document.getElementById("brand").onclick = () => run(door);
document.getElementById("cabinet").onclick = () =>
  run(async () => {
    state = await call("home");
    salon();
  });
document.getElementById("close").onclick = () => dialog.close();
async function boot() {
  tg = window.Telegram?.WebApp;
  tg?.ready();
  tg?.expand();
  tg?.BackButton?.onClick(back);
  state = await call("home");
  const q = new URLSearchParams(location.search);
  const key =
    tg?.initDataUnsafe?.start_param ||
    q.get("tgWebAppStartParam") ||
    q.get("startapp") ||
    "";
  const card = /^guru_c_([a-f0-9]{24})(?:_(\d+))?$/.exec(key);
  if (card) await shared(card[1], card[2]);
  else if (key === "guru_salon") salon();
  else if (key === "guru_door") await start();
  else door();
}
window.addEventListener("load", () =>
  run(async () => {
    door();
    await boot();
  }),
);
