"use strict";

let liveRoom;
let livePolling = false;

async function liveIndex() {
  const { room, recent } = await call("live_home");
  if (room) return renderLive(room);
  const el = panel("Живой приём");
  el.append(
    node("p", "Вы будете гадалкой. Друг по приглашению сядет за стол в роли клиента.", "muted"),
    group(
      button("Пригласить клиента", () => liveCommand("live_create"), "primary"),
      button("← В салон", refreshSalon),
    ),
  );
  if (recent)
    el.append(group(button("Итог последнего живого приёма", () => liveCommand("live_view", { id: recent }))));
  mount("live-index", el);
}

async function liveCommand(op, fields = {}) {
  const payload = await call(op, fields);
  if (payload.room) renderLive(payload.room);
}

async function refreshLive(force = false) {
  const room = liveRoom;
  if (!room || page !== "live" || livePolling) return;
  livePolling = true;
  try {
    const payload = await call("live_view", {
      id: room.id,
      rev: force ? null : room.rev,
      status: room.status,
    });
    if (payload.room && liveRoom === room && page === "live" && (force || !busy))
      renderLive(payload.room, true);
  } finally {
    livePolling = false;
  }
}

setInterval(() => {
  if (page === "live" && liveRoom?.status === "active" && liveRoom.role !== "invite" && !busy && !document.hidden)
    refreshLive().catch(() => {});
}, 1600);

async function playLive(room, action, text) {
  await liveCommand("live_move", { id: room.id, rev: room.rev, action, text });
  if (/^(pair|area):/.test(action)) feel("light");
}

function liveInput(room) {
  const form = node("form", "", "live-input");
  const label = node("label", room.input === "introduce" ? "Ваш рассказ" : "Ваш ответ");
  const input = node("textarea");
  input.maxLength = 1000;
  input.rows = 3;
  input.required = true;
  label.append(input);
  const submit = node("button", "Отправить мадам", "primary");
  submit.type = "submit";
  form.append(label, submit);
  form.onsubmit = (event) => {
    event.preventDefault();
    if (input.value.trim()) run(() => playLive(room, room.input, input.value));
  };
  if (room.input === "introduce") {
    const skip = button("Начать без рассказа", () => playLive(room, room.input, ""));
    skip.type = "button";
    form.append(skip);
  }
  return form;
}

function liveInvite(room) {
  const url = new URL(room.invite, location.href).href;
  const link = node("input");
  link.value = url;
  link.readOnly = true;
  link.setAttribute("aria-label", "Ссылка на живой приём");
  const send = button("Отправить в Telegram", () => tg.openTelegramLink(
    "https://t.me/share/url?" + new URLSearchParams({ url, text: "Приходи ко мне на живой приём в «Гуру»." }),
  ), "primary");
  const copy = button("Скопировать ссылку", async () => {
    try {
      await navigator.clipboard.writeText(url);
      say("Приглашение скопировано.");
    } catch {
      link.focus();
      link.select();
      say("Скопируйте выделенную ссылку.");
    }
  });
  modal("Приглашение клиенту", link, tg?.openTelegramLink ? group(send, copy) : group(copy));
}

function liveArt(room) {
  if (room.cards) {
    const art = node("div", "", "live-cards");
    for (const card of room.cards) {
      const open = button("", () => modal(card.title, tarotFace(card.art, card.title, "zoom-art")));
      open.setAttribute("aria-label", "Рассмотреть «" + card.title + "»");
      open.append(tarotFace(card.art, card.title));
      art.append(open);
    }
    return art;
  }
  if (room.palm) {
    const art = node("figure", "", "live-palm lamp-surface");
    art.append(img("live-palm-" + room.palm + ".jpg", "Ладонь клиента"));
    return art;
  }
}

function renderLive(room, keepScroll = false) {
  liveRoom = room;
  const el = panel("Живой приём");
  if (room.role === "invite") {
    el.append(node("p", room.host + " приглашает вас на приём в роли клиента."));
    el.append(group(button("Принять приглашение", () => liveCommand("live_join", { id: room.id }), "primary")));
  } else {
    const identity = room.role === "host"
      ? room.client ? "Ваш клиент · " + room.client : "Вы — гадалка"
      : "Ваша гадалка · " + room.host;
    el.append(node("p", identity, "meta"));
    if (room.status === "cancelled") {
      el.append(node("p", "Приём завершён досрочно."));
    } else if (room.status === "done") {
      el.append(node("h2", room.outcome));
      if (room.fee !== undefined) el.append(node("p", "Итог по кассе · " + rubles(room.fee), "salon-balance"));
    } else if (room.invite) {
      el.append(node("p", "Салон открыт. Отправьте ссылку тому, кому хотите погадать."));
      el.append(group(button("Поделиться приглашением", () => liveInvite(room), "primary")));
    }
    const talk = node("section", "", "paper live-talk");
    if (room.lines?.length) {
      const last = room.status === "done" ? room.lines.slice(-3) : room.lines.slice(-2);
      for (const line of last) talk.append(node("p", line, "live-line"));
      const diary = node("details");
      diary.append(node("summary", "Весь разговор"), ...room.lines.map(line => node("p", line, "live-line")));
      talk.append(diary);
    }
    const art = liveArt(room);
    if (art) talk.append(art);
    if (room.screen) talk.append(node("p", room.screen, "screen"));
    if (room.prompt) talk.append(node("p", room.prompt));
    if (room.prompt && !room.input && !room.actions.length)
      talk.append(node("p", "Ход другого игрока", "muted"));
    const privateNotes = room.drafts || room.intake;
    if (privateNotes?.length) {
      const notes = node("details");
      notes.append(node("summary", room.drafts ? "Мои черновики" : "Моя анкета"),
        ...privateNotes.map(line => node("p", line, "live-line")));
      talk.append(notes);
    }
    if (room.input) talk.append(liveInput(room));
    if (room.actions.length) {
      const actions = room.actions.map(a => button(a.label, () => playLive(room, a.id), a.kind === "method" ? "method" : ""));
      talk.append(group(...actions));
    }
    if (talk.childElementCount) el.append(talk);
  }
  const navigation = group(button("← В салон", refreshSalon));
  if (room.role !== "invite" && room.status === "active")
    navigation.append(button("Завершить приём", () => modal(
      "Завершить живой приём?",
      node("p", "Встреча закончится для обоих игроков."),
      group(button("Завершить", () => liveCommand("live_cancel", { id: room.id, rev: room.rev }), "primary"),
        button("Продолжить", () => dialog.close())),
    )));
  else if (room.status !== "active")
    navigation.append(button("Другой живой приём", liveIndex));
  el.append(navigation);
  mount("live", el, keepScroll);
}
