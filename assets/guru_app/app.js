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
  natal: "Натальная карта",
  tone: "Предсказание",
  reaction: "После ваших слов",
  final: "Напутствие",
};
const ritualNotes = {
  tarot: {
    asteroids:
      "Даже небесные тела перед важной встречей делают вид, что просто пролетали мимо.",
    beetle:
      "Жук везёт целый город. В отчёте это назвали распределением нагрузки.",
    bricks:
      "Кирпичи пересчитали. Теперь можно приступать к пересчёту пересчёта.",
    cactus:
      "Слоны поливают кактус с таким участием, будто засуха — личное оскорбление.",
    clocks:
      "Все часы показывают разное. Наконец-то можно опоздать с чистой совестью.",
    crows: "Вороны вернули ключи и письма. Чек за доставку, конечно, потеряли.",
    elephant:
      "На слоне держится дом. Слона на собрание жильцов всё равно не зовут.",
    fishes: "На жабе корона. На рыбке, как обычно, вся работа.",
    fox: "Зеркала протёрли. Объективности не прибавилось.",
    fruits: "Семейный стол. Главное блюдо — мнение о тех, кто не пришёл.",
    globe: "Дверь и ключ в наличии. Осталось выяснить, кто из них где.",
    heads:
      "Все головы говорят одновременно. Протокол, конечно, будет единогласным.",
    lemon:
      "У цапли есть уровень. У комиссии есть вопросы. Этого обычно хватает до обеда.",
    lighthouse: "Маяк работает исправно. Море пока не подписало акт приёмки.",
    mechanic:
      "Махина едет сама. Водитель, разумеется, всё держит под контролем.",
    porcelain: "Снаружи фарфор. Внутри, как обычно, требуется садовник.",
    sandfish:
      "Воды нет, зато есть дождевик. Подготовка признана удовлетворительной.",
    snail: "Город движется по графику. Сам график ползёт рядом.",
    sunflowers:
      "На ведре написано «Не трогать». Других приглашений не потребуется.",
    well: "Колодец всё помнит. Срок хранения у воды не установлен.",
    whale: "Кит сменил море на лес. Навигатор ещё ищет маршрут.",
  },
  palm: {
    apollo:
      "Аполлон просит посмотреть ещё раз. С этого ракурса он особенно хорош.",
    fate: "Линия судьбы любит планы. Особенно те, которые можно перенести на понедельник.",
    head: "Линия ума думает обо всём. Даже когда её об этом не просили.",
    heart: "Линия сердца опять написана без знаков препинания.",
    mars: "Марс предлагает досчитать до десяти. Сам он обычно успевает до двух.",
    venus: "Холм Венеры за удовольствие без спешки. Чай, впрочем, уже остыл.",
  },
  horoscope: {
    "♀": "Венера за откровенность. Желательно, чтобы первым признался кто-нибудь другой.",
    "♂": "Марс за прямой разговор. Слово «прямой» у него подозрительно часто означает «громкий».",
    "☿": "Меркурий опять пошёл вспять. Удобно, когда за опечатку отвечает планета.",
    "♃": "Юпитер обещает расширение. Остаётся уточнить, чего именно — доходов или расходов.",
    "♄": "Сатурн просит терпения. Срок ожидания он, разумеется, не назвал.",
    "☽": "Луна считает, что утро вечера мудренее. Сама она к утру обычно уходит.",
    "♅": "Уран отвечает за неожиданности. Предупреждать о визите он считает дурным тоном.",
  },
};
let tg,
  state,
  record,
  page,
  palmFocus,
  palmZoom = false,
  wheelAngle,
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
function tarotFace(art, title, cls = "") {
  const face = node("span", "", "tarot-face " + cls),
    ornament = node("span", "✧", "tarot-ornament");
  ornament.setAttribute("aria-hidden", "true");
  face.append(
    ornament,
    img("card-" + art + ".jpg", ""),
    node("span", title, "tarot-title"),
  );
  return face;
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
function mount(name, el, keepScroll = false) {
  if (dialog.open) dialog.close();
  page = name;
  main.replaceChildren(el);
  if (!keepScroll) window.scrollTo(0, 0);
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
async function start() {
  if (!state) state = await call("home");
  if (!state.current) state = await call("start");
  session();
}
function palmTool(s) {
  const { picture, zones, inspected, limit } = s.palm,
    art = node("div", "", "palm-tool"),
    frame = node("div", "", "palm-frame"),
    zoomer = node("div", "", "palm-zoomer"),
    panel = node("section", "", "palm-reading"),
    detail = node("div"),
    list = node("details", "", "palm-list"),
    marks = group();
  const drawing = img(picture, "Ладонь клиента");
  drawing.draggable = false;
  drawing.width = 500;
  drawing.height = 500;
  zoomer.append(drawing);
  const whole = button(
    "Вся ладонь",
    () => {
      palmZoom = false;
      update();
    },
    "palm-reset",
  );
  palmFocus ||= inspected.at(-1);
  const markers = zones.map((zone, i) => {
    const choose = () => {
      palmFocus = zone.id;
      palmZoom = true;
      update();
    };
    const inspectedZone = inspected.includes(zone.id);
    const spot = button(
      inspectedZone ? "✓" : String(i + 1),
      choose,
      "palm-spot",
    );
    spot.setAttribute("aria-label", "Участок: " + zone.title);
    spot.title = zone.title;
    if (inspectedZone) spot.classList.add("inspected");
    spot.style.left = zone.point[0] + "%";
    spot.style.top = zone.point[1] + "%";
    const label = button(zone.title, choose);
    zoomer.append(spot);
    marks.append(label);
    return { zone, spot, label };
  });
  function update() {
    const zone = zones.find((z) => z.id === palmFocus);
    zoomer.style.setProperty("--palm-zoom", palmZoom ? "1.45" : "1");
    zoomer.style.setProperty("--spot-zoom", palmZoom ? String(1 / 1.45) : "1");
    if (zone)
      zoomer.style.transformOrigin = zone.point.map((p) => p + "%").join(" ");
    whole.hidden = !palmZoom;
    for (const marker of markers) {
      marker.spot.setAttribute("aria-pressed", marker.zone === zone);
      marker.label.setAttribute("aria-pressed", marker.zone === zone);
    }
    detail.replaceChildren();
    if (!zone) return;
    detail.append(node("h3", zone.title));
    if (zone.finding) {
      detail.append(node("p", zone.finding));
      if (zone.remark) detail.append(node("p", "— " + zone.remark));
      detail.append(node("p", ritualNotes.palm[zone.id], "ritual-note"));
    } else if (s.actions.some((a) => a.id === zone.action)) {
      detail.append(
        button("Осмотреть этот участок", () => move(zone.action), "primary"),
      );
    } else if (inspected.length >= limit) {
      detail.append(node("p", "Клиент уже убирает руку."));
    }
  }
  list.append(node("summary", "Список участков"), marks);
  panel.append(
    node(
      "p",
      "Осмотрено " +
        inspected.length +
        " из " +
        limit +
        " · Выберите участок на ладони.",
      "meta",
    ),
    detail,
    list,
  );
  frame.append(zoomer);
  art.append(frame, whole);
  update();
  return { art, panel };
}

const zodiac = Array.from("♈♉♊♋♌♍♎♏♐♑♒♓");
function positionOnWheel(el, angle, radius) {
  el.style.left = 50 + radius * Math.sin((angle * Math.PI) / 180) + "%";
  el.style.top = 50 - radius * Math.cos((angle * Math.PI) / 180) + "%";
}

function horoscopeWheel(s, animate) {
  const { planets, selected } = s.natal || s.horoscope,
    wheel = node("div", "", "wheel"),
    rotor = node("div", "", "wheel-rotor"),
    step = 360 / planets.length,
    previous = wheelAngle ?? step / 2,
    target = selected === null ? step / 2 : -selected * step,
    turn = previous + ((((target - previous + 180) % 360) + 360) % 360) - 180;
  wheelAngle = turn;
  const spin = (element, from, to) => {
    element.style.transform = to;
    if (
      animate &&
      turn !== previous &&
      !matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      element.animate([{ transform: from }, { transform: to }], {
        duration: 560,
        easing: "cubic-bezier(.23,.72,.2,1)",
      });
    }
  };
  zodiac.forEach((glyph, i) => {
    const tick = node("span", glyph + "\ufe0e", "zodiac-tick");
    tick.setAttribute("aria-hidden", "true");
    positionOnWheel(tick, i * 30, 44);
    wheel.append(tick);
  });
  if (s.natal && selected !== null) {
    const radians = (selected * step * Math.PI) / 180,
      dx = 35 * Math.sin(radians),
      dy = 35 * (1 - Math.cos(radians)),
      link = node("span", "", "wheel-aspect");
    positionOnWheel(link, 0, 35);
    link.style.width = Math.hypot(dx, dy) + "%";
    link.style.transform = "rotate(" + Math.atan2(dy, dx) + "rad)";
    rotor.append(link);
  }
  planets.forEach((p, i) => {
    const active = s.actions.some((a) => a.id === p.id);
    const planet = active
      ? button(p.glyph, () => move(p.id), "planet")
      : node("span", p.glyph, "planet");
    const label =
      s.natal?.aspects.find((aspect) => aspect.id === p.id)?.label ||
      p.glyph + " " + p.name;
    if (active) planet.setAttribute("aria-label", "На колесе: " + label);
    else planet.setAttribute("aria-hidden", "true");
    planet.title = label;
    if (i === selected || (s.natal && selected !== null && i === 0))
      planet.classList.add("selected");
    positionOnWheel(planet, i * step, 35);
    const orientation = (angle) =>
      "translate(-50%, -50%) rotate(" + -angle + "deg)";
    spin(planet, orientation(previous), orientation(turn));
    rotor.append(planet);
  });
  const rotation = (angle) => "rotate(" + angle + "deg)";
  spin(rotor, rotation(previous), rotation(turn));
  wheel.append(
    rotor,
    node("i", "", "wheel-pointer"),
    node("span", "✷", "wheel-pin"),
  );
  return wheel;
}

function natalAspects(s) {
  const list = node("div", "", "natal-aspects");
  s.natal.aspects.forEach((aspect, index) => {
    const option = button(aspect.label, () => move(aspect.id));
    option.append(node("small", aspect.house));
    option.setAttribute("aria-pressed", String(s.natal.selected === index + 1));
    list.append(option);
  });
  return list;
}

function natalQuotes(s) {
  const list = node("div", "", "natal-quotes");
  for (const action of s.actions.filter((a) => a.kind === "quote"))
    list.append(button(s.visualClues ? action.label : "«" + action.label + "»", () => move(action.id)));
  if (!s.natal.quote) return list;
  const details = node("details");
  details.append(node("summary", s.visualClues ? "Выбрать другую деталь" : "Выбрать другую фразу"), list);
  const panel = node("div");
  panel.append(node("blockquote", s.natal.quote, "natal-quote"), details);
  return panel;
}

function salonBackdrop(art) {
  art.style.setProperty(
    "--salon-scene",
    'url("' + asset(state.salon.picture) + '")',
  );
  return art;
}
function greeting(s) {
  const el = node("section", "", s.bonus ? "greeting bonus-greeting" : "greeting"),
    art = salonBackdrop(node("div", "", "greeting-art")),
    copy = node("article", "", "greeting-copy");
  art.append(img(portraitFile(s.story), "Портрет гостя"));
  if (s.bonus) copy.append(node("p", "✦ Гость из чата", "bonus-mark"));
  copy.append(
    node("h1", s.client),
    node("p", "— " + (s.greeting || "Здравствуйте, мадам."), "speech"),
    group(
      button(
        "Начать сеанс →",
        async () => {
          state = await call("greet");
          session();
        },
        "primary",
      ),
      button("Выйти из приёма", pauseVisit, "quiet"),
    ),
  );
  el.append(art, copy);
  mount("greeting", el);
}

function sceneArt(s, hand, keepScroll) {
  const art = salonBackdrop(node("aside", "", "stage-art"));
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
    art.classList.add("tarot-stage");
    const zoom = button(
      "",
      () =>
        modal("Карта крупным планом", tarotFace(s.art, s.artTitle, "zoom-art")),
      "zoom",
    );
    zoom.setAttribute("aria-label", "Рассмотреть рисунок карты");
    zoom.append(tarotFace(s.art, s.artTitle));
    art.append(zoom);
  } else if (hand) {
    art.classList.add("palm-stage");
    art.append(hand.art);
    return art;
  } else if (s.horoscope || s.natal) {
    art.classList.add("wheel-stage");
    art.append(horoscopeWheel(s, keepScroll));
    return art;
  } else {
    art.classList.add("visitor-stage");
    if (s.story === "jealous.vocal") {
      const portrait =
        { angry: "irritated", hopeful: "relieved", tired: "suspicious" }[
          s.mood
        ] || "base";
      art.append(img(portrait + ".webp", s.client, "portrait"));
    } else art.append(img(portraitFile(s.story), s.client, "cameo"));
  }
  art.append(node("span", s.client, "plate"));
  return art;
}
async function move(id) {
  const previous = state.current;
  state = await call("move", { action: id });
  if (state.record) {
    const firstVisit = !state.history.some(
      (visit) =>
        visit.story === state.record.story && visit.id !== state.record.id,
    );
    showRecord(state.record, firstVisit);
  } else
    session(
      Boolean(
        (previous.palm && state.current.palm) ||
        ((previous.horoscope || previous.natal) &&
          (state.current.horoscope || state.current.natal) &&
          state.current.phase !== "tone"),
      ),
    );
}
function session(keepScroll = false) {
  const s = state.current;
  if (!s) return salon();
  if (s.greetingPending) return greeting(s);
  if (!s.palm) {
    palmFocus = undefined;
    palmZoom = false;
  }
  if (!s.horoscope && !s.natal) wheelAngle = undefined;
  const hand = s.palm ? palmTool(s) : null;
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
  for (const line of s.palm?.inspected.length ? [] : s.lines)
    speech.append(node("p", line.replace(/^🚪\s*/u, "")));
  text.append(speech);
  if (hand) text.append(hand.panel);
  if (s.horoscope)
    text.append(node("p", s.horoscope.sign + " · Эта неделя", "meta"));
  if (s.screen && !["palm", "horoscope"].includes(s.phase))
    text.append(node("p", s.screen, "screen"));
  const note =
    s.phase === "tarot"
      ? ritualNotes.tarot[s.art]
      : s.phase === "forecast"
        ? ritualNotes.horoscope[
            s.horoscope.planets[s.horoscope.selected]?.glyph
          ]
        : null;
  if (note) text.append(node("p", note, "ritual-note"));
  if (s.natal) {
    text.append(node("p", "Одна астрологическая практика за сеанс", "meta"));
    if (s.natal.tension)
      text.append(
        natalAspects(s),
        node("p", s.natal.tension, "natal-tension"),
        node("p", s.natal.note, "ritual-note"),
      );
  }
  text.append(node("div", s.prompt, "prompt"));
  if (s.natal) text.append(s.natal.tension ? natalQuotes(s) : natalAspects(s));
  const choices = node("div", "", "actions");
  const rest = node("details"),
    secondary = node("div", "", "actions");
  rest.append(node("summary", "Другие вопросы и практики"), secondary);
  for (const a of s.actions) {
    if (["zone", "sign", "aspect", "quote"].includes(a.kind)) continue;
    const container =
      s.opening.length && !s.opening.includes(a.id) ? secondary : choices;
    container.append(
      button(
        a.label,
        () => move(a.id),
        a.kind === "method"
          ? "method"
          : a.id === "scene"
            ? "primary"
            : a.id === "weekly" || a.id === "natal"
              ? "quiet"
              : "",
      ),
    );
  }
  if (s.horoscope && s.phase !== "tone") {
    const planets = node("details", "", "planet-list"),
      rows = node("div", "", "choice-list");
    planets.open = s.phase === "horoscope";
    for (const p of s.horoscope.planets) {
      if (s.actions.some((a) => a.id === p.id)) {
        const option = button(p.glyph + " " + p.name, () => move(p.id));
        option.append(node("small", p.theme));
        rows.append(option);
      }
    }
    planets.append(node("summary", "Планеты и тенденции"), rows);
    text.append(planets);
  }
  const journal = node("details");
  journal.append(
    node(
      "summary",
      s.visualClues ? "Что удалось заметить" : s.opening.length ? "Выслушать весь рассказ" : "Что прозвучало",
    ),
    ...s.heard.map((line) => node("p", line)),
  );
  text.append(choices);
  if (secondary.childElementCount) text.append(rest);
  text.append(journal, button("Выйти из приёма", pauseVisit, "quiet back"));
  el.append(sceneArt(s, hand, keepScroll), text);
  mount("session", el, keepScroll);
}
function pauseVisit() {
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
  );
}
function ticket(title, detail, action, picture) {
  const el = button("", action, "ticket"),
    copy = node("span");
  if (picture) el.append(img(picture, ""));
  copy.append(node("small", detail), node("strong", title));
  el.append(copy);
  return el;
}
const rubles = (amount) => amount.toLocaleString("ru") + " ₽";

function salon() {
  if (!state) return door();
  const el = panel("Мой салон"),
    room = node("section", "", "salon-room"),
    frame = node("figure", "", "salon-picture"),
    summary = node("div", "", "salon-summary");
  summary.append(
    node("h2", state.salon.rank),
    node("p", "Касса · " + rubles(state.salon.balance), "salon-balance"),
  );
  if (state.history.length || state.salon.collection.length)
    summary.append(
      node(
        "p",
        "Доходы с учётом возвратов · " + rubles(state.salon.fees),
        "cash-line",
      ),
      node("p", "Потрачено в лавке · " + rubles(state.salon.spent), "cash-line"),
    );
  summary.append(
    group(
      button(
        state.current
          ? "Продолжить · " + state.current.client
          : "Принять посетителя",
        () => start(),
        "primary",
      ),
      button("Открыть лавку", shop),
      button("Топ гадалок", () => leaderboard()),
      button(
        "Книга посетителей · " +
          state.openedCount +
          "/" +
          state.count,
        guests,
      ),
    ),
  );
  frame.append(img(state.salon.picture, "Интерьер вашего салона"));
  room.append(frame, summary);
  el.append(room);
  if (state.salon.collection.length) {
    const purchases = node("details", "", "salon-purchases"),
      list = node("div", "", "gift-list");
    for (const item of state.salon.collection)
      list.append(button(item.name, () => previewItem(item)));
    purchases.append(node("summary", "Покупки салона · " + state.salon.collection.length), list);
    el.append(purchases);
  }
  el.append(shelf());
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

function shelf() {
  const section = node("section", "", "gift-shelf"),
    list = node("div", "", "gift-list"),
    gifts = state.salon.gifts,
    owned = gifts.filter((gift) => gift.story);
  section.append(
    node("h2", "Полка подарков"),
    node(
      "p",
      owned.length +
        " из " +
        gifts.length +
        " · Подарки оставляют довольные клиенты.",
      "muted",
    ),
  );
  for (const gift of owned) {
    list.append(
      ticket(gift.name, gift.emoji + " · Подарок от " + gift.giver, () =>
        modal(gift.name, node("p", gift.story)),
      ),
    );
  }
  section.append(list);
  return section;
}

// The item's base anchors it to the floor or the surface it rests on.
// The four values are x, y, maximum width and maximum height, in room percentages.
const displayPositions = {
  // The entrance and its ledge.
  zina: [13, 85.5, 14, 62],
  flyers: [21.1, 45.6, 3.8, 5.5],
  neon: [21.1, 29, 4, 10],
  // The short high shelf and the instruments below it.
  clock: [27.4, 22, 5.1, 11.5],
  almanac: [34.2, 22, 6.6, 6.7],
  reviews: [40.2, 22, 4.6, 5.6],
  astrolabe: [36.4, 33.7, 5.7, 10.5],
  orrery: [48.1, 33.7, 12.3, 16.5],
  ball: [58.2, 33.7, 6.6, 11],
  cabinet: [69, 70.3, 10.2, 43],
  // Far and near edges of the worktable.
  candles: [26.5, 49.8, 7.3, 14],
  bowl: [43.3, 49.2, 8.5, 7.4],
  atlas: [55.9, 49.3, 10.4, 10.2],
  deck: [32.7, 53.6, 7.5, 7.2],
  ledger: [40, 53.8, 6.1, 8],
  loupe: [48.7, 53.6, 8, 5.3],
  silk: [57.5, 53.8, 10.3, 6.7],
  // The wall, side table and rug in the waiting nook.
  tapestry: [80.6, 44.2, 9.1, 28],
  tea: [86.6, 47.6, 8.4, 9.2],
  mirror: [94.4, 48.7, 8.3, 23.5],
  armchair: [80.6, 81.9, 17.5, 38.5],
  cat: [93.3, 89.5, 12.4, 13.8],
};

function previewItem(item) {
  modal(item.name, img(item.picture, "", "object-preview"));
}

function shopDisplay(total, featured) {
  const section = node("section", "", "shop-display"),
    viewport = node("div", "", "showroom-viewport"),
    room = node("div", "", "showroom"),
    owned = state.salon.collection,
    background = img(
      "showroom.jpg",
      "Вход, рабочий стол и уголок для гостей в лавке",
      "showroom-backdrop",
    );
  background.width = 1500;
  background.height = 844;
  room.append(background);
  let newItem;
  for (const item of owned) {
    const [x, bottom, width, height] = displayPositions[item.id];
    const prop = button("", () => previewItem(item), "showroom-item");
    prop.setAttribute("aria-label", item.name);
    prop.title = item.name;
    prop.dataset.item = item.id;
    Object.assign(prop.style, {
      left: x + "%",
      top: bottom + "%",
      width: width + "%",
      height: height + "%",
      zIndex: String(Math.round(bottom * 10)),
    });
    prop.append(img("display-" + item.id + ".webp", ""));
    if (item.id === featured) {
      prop.classList.add("new-showroom-item");
      newItem = prop;
    }
    room.append(prop);
  }
  const zoom = button(
    "Приблизить витрину",
    () => {
      const enlarged = room.classList.toggle("showroom-enlarged");
      zoom.textContent = enlarged
        ? "Показать всю витрину"
        : "Приблизить витрину";
      zoom.setAttribute("aria-pressed", enlarged);
      viewport.tabIndex = enlarged ? 0 : -1;
      viewport.scrollLeft = enlarged
        ? (viewport.scrollWidth - viewport.clientWidth) / 2
        : 0;
    },
    "quiet",
  );
  zoom.setAttribute("aria-pressed", "false");
  viewport.setAttribute("role", "region");
  viewport.setAttribute("aria-label", "Витрина лавки");
  viewport.append(room);
  section.append(
    viewport,
    node("p", "На витрине " + owned.length + " из " + total, "muted"),
    zoom,
  );
  return { section, newItem };
}

async function shop() {
  state = await call("home");
  renderShop(await call("shop"));
}

function renderShop(catalog, featured) {
  const { shop: groups, freeShop } = catalog,
    el = panel("Лавка"),
    navigation = group(button("← В салон", salon)),
    display = shopDisplay(
      groups.reduce((sum, group) => sum + group.items.length, 0),
      featured,
    );
  el.append(
    display.section,
    node("p", "Касса · " + rubles(state.salon.balance), "salon-balance"),
  );
  if (freeShop)
    el.append(node("p", "Локальная проверка · покупки за 0 ₽", "muted"));
  el.append(navigation);
  for (const { title, items } of groups) {
    const box = node("section", "", "shop-section"),
      grid = node("div", "", "grid");
    box.append(node("h2", title));
    navigation.append(
      button(title, () => box.scrollIntoView({ block: "start" })),
    );
    for (const item of items) {
      const tile = node("article", "", "shop-item"),
        owned = state.salon.items.includes(item.id),
        gap = item.price - Math.max(0, state.salon.balance);
      const portrait = img(item.picture, "", "shop-object");
      portrait.loading = "lazy";
      tile.append(
        portrait,
        node("h3", item.name),
        node("p", item.note, "muted"),
      );
      if (owned) {
        tile.append(
          button(
            "На витрине · Рассмотреть",
            () => previewItem(item),
            "owned-item",
          ),
        );
      } else if (gap > 0) {
        tile.append(
          node(
            "p",
            rubles(item.price) + " · Не хватает " + rubles(gap),
            "meta",
          ),
        );
      } else {
        tile.append(
          button("Купить · " + rubles(item.price), () =>
            confirmPurchase(item, catalog),
          ),
        );
      }
      grid.append(tile);
    }
    box.append(grid);
    el.append(box);
  }
  mount("shop", el);
  display.newItem?.focus({ preventScroll: true });
}
function confirmPurchase(item, catalog) {
  modal(
    item.name,
    img(item.picture, item.name, "object-preview"),
    node("p", item.note),
    group(
      button(
        "Купить за " + rubles(item.price),
        async () => {
          state = await call("buy", { item: item.id });
          renderShop(catalog, item.id);
        },
        "primary",
      ),
      button("Отмена", () => dialog.close()),
    ),
  );
}

async function leaderboard(period = "week") {
  renderLeaderboard(period, await call("leaderboard", { period }));
}

function rankRow(entry) {
  const row = node("li", "", entry.self ? "rank-row rank-self" : "rank-row"),
    name = node("div", "", "rank-name");
  name.append(
    node("strong", entry.name),
    node("small", "Приёмов: " + entry.visits + (entry.self ? " · Вы" : "")),
  );
  row.append(
    node("span", String(entry.place), "rank-position"),
    name,
    node("strong", rubles(entry.earned), "rank-earned"),
  );
  return row;
}

function renderLeaderboard(period, board) {
  const el = panel("Топ гадалок"),
    tabs = group(),
    ranks = node("ol", "", "rank-list");
  for (const [id, title] of [
    ["week", "Последние 7 дней"],
    ["all", "За всё время"],
  ]) {
    const tab = button(
      title,
      () => leaderboard(id),
      period === id ? "primary" : "",
    );
    tab.setAttribute("aria-pressed", period === id);
    tabs.append(tab);
  }
  el.append(
    node(
      "p",
      "В рейтинг входят гонорары за приёмы в Mini App за вычетом возвратов.",
      "muted",
    ),
    tabs,
  );
  if (board.leaders.length) {
    ranks.append(...board.leaders.map(rankRow));
    el.append(ranks);
  } else
    el.append(
      node("p", "За этот период ещё никто не завершил приём.", "muted"),
    );
  if (board.own && !board.leaders.some((entry) => entry.self)) {
    const own = node("ol", "", "rank-list");
    own.append(rankRow(board.own));
    el.append(own);
  }
  el.append(
    group(
      button("Имя в рейтинге", () => changeRankName(period, board.alias, board.profileName)),
      button("← В салон", salon),
    ),
  );
  mount("leaderboard", el);
}

function changeRankName(period, alias, profileName) {
  const form = node("form", "", "alias-form"),
    label = node("label", "Публичное имя"),
    input = node("input"),
    save = node("button", "Сохранить", "primary");
  input.value = alias;
  input.maxLength = 28;
  input.placeholder = profileName || "Имя из Telegram";
  label.append(input);
  form.append(label, save);
  form.onsubmit = (event) => {
    event.preventDefault();
    run(async () =>
      renderLeaderboard(
        period,
        await call("rename", { period, name: input.value }),
      ),
    );
  };
  modal("Имя в рейтинге", form);
  input.focus();
}
async function guests(revealedId) {
  const { catalog, bonusCatalog, bonusInterval } = await call("catalog"),
    el = panel("Книга посетителей");
  el.append(
    group(
      button(
        state.current ? "Продолжить текущий приём" : "Открыть дверь →",
        start,
        "primary",
      ),
      button("← В салон", salon),
    ),
  );
  let revealed;
  for (const [entries, bonus] of [
    [bonusCatalog, true],
    [catalog, false],
  ]) {
    const opened = entries.filter(Boolean),
      locked = entries.length - opened.length,
      section = node("section", "", bonus ? "bonus-book" : ""),
      grid = node("ul", "", "guest-book");
    section.append(
      node("h2", bonus ? "✦ Гости из чата" : "Посетители салона"),
      node("p", "Открыто " + opened.length + " из " + entries.length, "salon-balance"),
      node(
        "p",
        bonus
          ? "Гость из чата приходит после каждых " + bonusInterval + " завершённых обычных приёмов. Портрет появляется при встрече."
          : "Портрет открывается после первой завершённой встречи.",
        "muted",
      ),
    );
    for (const guest of [...opened, ...Array(locked).fill(null)]) {
      const page = node("li", "", guest ? "guest-page" : "guest-page unopened"),
        frame = node("div", "", "guest-frame");
      if (guest) {
        const portrait = img(portraitFile(guest.id), "");
        portrait.loading = "lazy";
        frame.append(portrait);
        page.append(frame, node("strong", guest.name));
        if (guest.id === revealedId) {
          page.classList.add("new-portrait");
          page.tabIndex = -1;
          portrait.loading = "eager";
          revealed = page;
        }
      } else {
        frame.setAttribute("aria-hidden", "true");
        if (bonus) frame.textContent = "✦";
        page.append(frame, node("span", bonus ? "Тайный гость" : "Портрет закрыт", "guest-unopened-label"));
      }
      grid.append(page);
    }
    section.append(grid);
    el.append(section);
  }
  mount("guests", el);
  if (revealed) {
    revealed.scrollIntoView({ block: "nearest" });
    revealed.focus({ preventScroll: true });
  }
}
function showRecord(r, firstVisit = false) {
  record = r;
  const el = panel(r.title),
    body = node("article", "", "paper record");
  if (r.story === "jealous.vocal" && r.ending === "duet")
    body.append(img("ending-duet.webp", "Финал в ресторане", "arcana"));
  body.append(
    node("div", r.bonus ? "✦ Гость из чата · " + r.client : r.client, "meta"),
    node("p", r.text),
    node("p", r.quote),
    node("p", r.afterword),
  );
  if (r.natal) {
    const chart = node("details");
    chart.append(
      node("summary", "Натальная карта · " + r.natal.aspect),
      node("blockquote", r.natal.quote, "natal-quote"),
      node("p", r.natal.question),
      node("p", "— " + r.natal.reply),
    );
    if (r.natal.afterword) chart.append(node("p", r.natal.afterword));
    body.append(chart);
  }
  const payment = node("section", "", "visit-payment");
  payment.append(
    node(
      "strong",
      (r.fee < 0 ? "Возврат · " : "Гонорар · ") + rubles(Math.abs(r.fee)),
    ),
  );
  if (r.payment) {
    const { base, rate, bonus } = r.payment;
    const reason = {
      150: "Клиент доволен и признал попадание",
      100: "Клиент доволен",
      60: "Клиент ушёл со смешанным впечатлением",
      20: "Клиент недоволен",
      "-50": "Совет привёл к неприятным последствиям",
    }[rate];
    payment.append(
      node(
        "p",
        reason +
          " · " +
          Math.abs(rate) +
          "% от " +
          rubles(base) +
          (bonus ? " · Реклама +" + rubles(bonus) : ""),
      ),
    );
  }
  body.append(payment);
  const breakdown = node("details");
  breakdown.append(
    node("summary", "Что сбылось и почему"),
    node("p", r.epilogue),
  );
  body.append(breakdown);
  if (r.keepsake) body.append(node("p", "На память · " + r.keepsake));
  el.append(body);
  if (firstVisit && !r.bonus)
    el.append(group(button("Новый портрет в книге →", () => guests(r.story))));
  if (r.cards.length) {
    el.append(node("h2", "Карта для чата"));
    const list = node("div", "", "grid");
    r.cards.forEach((c, slot) => {
      const card = ticket(
        c.title,
        c.symbol,
        () => compose(r.id, slot, c),
        "card-" + c.art + ".jpg",
      );
      card.classList.add("tarot-ticket");
      list.append(card);
    });
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
    tarotFace(c.art, c.title, "shared-face"),
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
  spread.append(tarotFace(c.art, c.title, "shared-face"), copy);
  el.append(spread, button("Открыть свой салон →", salon, "primary"));
  mount("shared", el);
}
function back() {
  if (dialog.open) dialog.close();
  else if (
    [
      "record",
      "guests",
      "greeting",
      "letter",
      "session",
      "shared",
      "shop",
      "leaderboard",
    ].includes(page)
  )
    salon();
  else door();
}
document.getElementById("brand").onclick = () => run(door);
async function refreshSalon() {
  state = await call("home");
  salon();
}
document.getElementById("cabinet").onclick = () => run(refreshSalon);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  if (page === "salon") run(refreshSalon);
  else if (page === "shop") run(shop);
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
  else if (key === "guru_shop") renderShop(await call("shop"));
  else if (key === "guru_door") await start();
  else door();
}
window.addEventListener("load", () =>
  run(async () => {
    door();
    await boot();
  }),
);
