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
  roomUrl,
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
  return op === "interior" ? res.blob() : res.json();
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
async function start(story) {
  if (!state) state = await call("home");
  if (!state.current) state = await call("start", story ? { story } : {});
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
  drawing.width = picture === "palm.webp" ? 1254 : 738;
  drawing.height = picture === "palm.webp" ? 1254 : 1100;
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
  const { planets, selected } = s.horoscope,
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
  planets.forEach((p, i) => {
    const active = s.actions.some((a) => a.id === p.id);
    const planet = active
      ? button(p.glyph, () => move(p.id), "planet")
      : node("span", p.glyph, "planet");
    const label = p.glyph + " " + p.name;
    if (active) planet.setAttribute("aria-label", "На колесе: " + label);
    else planet.setAttribute("aria-hidden", "true");
    planet.title = label;
    if (i === selected) planet.classList.add("selected");
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

function sceneArt(s, hand, keepScroll) {
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
  } else if (hand) {
    art.classList.add("palm-stage");
    art.append(hand.art);
    return art;
  } else if (s.horoscope) {
    art.classList.add("wheel-stage");
    art.append(horoscopeWheel(s, keepScroll));
    return art;
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
  const previous = state.current;
  state = await call("move", { action: id });
  if (state.record) showRecord(state.record);
  else
    session(
      Boolean(
        (previous.palm && state.current.palm) ||
        (previous.horoscope &&
          state.current.horoscope &&
          state.current.phase !== "tone"),
      ),
    );
}
function session(keepScroll = false) {
  const s = state.current;
  if (!s) return salon();
  if (!s.palm) {
    palmFocus = undefined;
    palmZoom = false;
  }
  if (!s.horoscope) wheelAngle = undefined;
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
  text.append(node("div", s.prompt, "prompt"));
  const choices = node("div", "", "actions");
  const rest = node("details"),
    secondary = node("div", "", "actions");
  rest.append(node("summary", "Другие вопросы и практики"), secondary);
  for (const a of s.actions) {
    if (a.kind === "zone" || a.kind === "sign") continue;
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
  el.append(sceneArt(s, hand, keepScroll), text);
  mount("session", el, keepScroll);
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

async function drawRoom(frame) {
  try {
    const picture = await call("interior");
    if (!frame.isConnected) return;
    if (roomUrl) URL.revokeObjectURL(roomUrl);
    roomUrl = URL.createObjectURL(picture);
    const room = node("img");
    room.src = roomUrl;
    room.alt = "Интерьер салона с покупками и подарками";
    frame.replaceChildren(room);
  } catch {
    frame.textContent = "Не удалось загрузить рисунок салона.";
  }
}

function salon() {
  if (!state) return door();
  const el = panel("Мой салон"),
    room = node("section", "", "salon-room"),
    frame = node("figure", "Зажигаем свет…", "salon-picture"),
    summary = node("div", "", "salon-summary");
  summary.append(
    node("h2", state.salon.rank),
    node("p", "Касса · " + rubles(state.salon.balance), "salon-balance"),
    node("p", "Покупок в салоне: " + state.salon.items.length, "muted"),
    group(
      button(
        state.current
          ? "Продолжить · " + state.current.client
          : "Принять посетителя",
        () => start(),
        "primary",
      ),
      button("Открыть лавку", shop),
      button("Книга посетителей · " + state.count, guests),
    ),
  );
  room.append(frame, summary);
  el.append(room, shelf());
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
  drawRoom(frame);
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
async function shop() {
  state = await call("home");
  const { shop: groups } = await call("shop"),
    el = panel("Лавка");
  el.append(
    node("p", "Касса · " + rubles(state.salon.balance), "salon-balance"),
    group(button("← Вернуться в салон", salon)),
  );
  for (const { title, items } of groups) {
    const box = node("section", "", "shop-section"),
      grid = node("div", "", "grid");
    box.append(node("h2", title));
    for (const item of items) {
      const tile = node("article", "", "shop-item"),
        owned = state.salon.items.includes(item.id),
        gap = item.price - Math.max(0, state.salon.balance);
      tile.append(
        node("h3", item.emoji + " " + item.name),
        node("p", item.note, "muted"),
      );
      if (owned) {
        tile.append(node("p", "✓ Уже в салоне", "owned-item"));
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
          button("Купить · " + rubles(item.price), () => confirmPurchase(item)),
        );
      }
      grid.append(tile);
    }
    box.append(grid);
    el.append(box);
  }
  mount("shop", el);
}
function confirmPurchase(item) {
  modal(
    item.name,
    node("p", item.note),
    group(
      button(
        "Купить за " + rubles(item.price),
        async () => {
          state = await call("buy", { item: item.id });
          salon();
          say("Покупка уже в салоне.");
        },
        "primary",
      ),
      button("Отмена", () => dialog.close()),
    ),
  );
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
  else if (
    ["record", "guests", "letter", "session", "shared", "shop"].includes(page)
  )
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
