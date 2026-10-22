(() => {
  "use strict";

  const PHRASES = [
    "чо надо",
    "не тыкай",
    "я червяк",
    "сыр отменяется",
    "хочу в суп",
    "мне похуй",
    "нарисуй меня",
    "кракадил?",
    "пиздиш",
    "кто тут батя",
    "ещё раз — и бан",
    "я не бот, я упупа",
    "upupa epops, между прочим",
  ];

  // Упупа пишет «кракадил», «чят» и «онализ», а человек ищет «крокодил»,
  // «чат» и «анализ». Сводим похожие гласные к одной, длина строки не меняется.
  const VOWELS = { "ё": "и", "е": "и", "э": "и", "ы": "и", "о": "а", "я": "а", "ю": "у", "щ": "ш" };
  const norm = (text) => text.toLowerCase().replace(/[ёеэыояющ]/g, (ch) => VOWELS[ch]);

  const $ = (selector, root = document) => root.querySelector(selector);

  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else node.setAttribute(key, value);
    }
    node.append(...children);
    return node;
  };

  /* ---------- копирование команд ---------- */

  const toast = $(".toast");
  let toastTimer = 0;

  const showToast = (text) => {
    toast.textContent = text;
    toast.classList.add("is-on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("is-on"), 1800);
  };

  const copyCommand = async (command) => {
    try {
      await navigator.clipboard.writeText(command);
      showToast(`скапировал: ${command}`);
    } catch {
      showToast(`напиши в чяте: ${command}`);
    }
  };

  document.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-cmd]");
    if (chip) copyCommand(chip.dataset.cmd);
  });

  /* ---------- тыкнуть Упупу ---------- */

  const bird = $(".bird");
  const bubble = $(".bubble");
  let lastPhrase = 0;

  bird.addEventListener("click", () => {
    let next = lastPhrase;
    while (next === lastPhrase) next = Math.floor(Math.random() * PHRASES.length);
    lastPhrase = next;
    bubble.textContent = PHRASES[next];
    bird.classList.remove("is-poked");
    void bird.offsetWidth;
    bird.classList.add("is-poked");
  });

  /* ---------- справочник ---------- */

  let sections = [];
  try {
    sections = JSON.parse($("#help-data").textContent);
  } catch {
    sections = [];
  }

  const refBody = $(".ref-body");
  const refCount = $(".ref-count");
  const tabs = $(".tabs");
  const search = $("#ref-search");

  if (!Array.isArray(sections) || !sections.length) {
    $(".ref-tools").hidden = true;
    refBody.append(el("p", { class: "ref-empty", text: "Справочник куда-то делся. Напиши боту «справка»." }));
    return;
  }

  const plain = (html) => el("div", { html }).textContent;
  const allCommands = new Set();

  for (const section of sections) {
    section.hay = norm(section.title);
    for (const group of section.groups) {
      group.hay = norm(group.title);
      for (const item of group.items) {
        item.cmds.forEach((command) => allCommands.add(command));
        item.hay = norm([item.cmds.join(" "), item.qual, plain(item.desc)].join(" "));
      }
    }
  }

  const commandsNoun = (count) => {
    const tens = count % 100;
    const ones = count % 10;
    if (tens >= 11 && tens <= 14) return "команд";
    if (ones === 1) return "команда";
    if (ones >= 2 && ones <= 4) return "команды";
    return "команд";
  };

  document.querySelectorAll("[data-count]").forEach((node) => {
    node.textContent = String(allCommands.size);
  });
  document.querySelectorAll("[data-count-noun]").forEach((node) => {
    node.textContent = commandsNoun(allCommands.size);
  });

  const highlight = (root, words) => {
    if (!words.length) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    for (const node of nodes) {
      const text = node.nodeValue;
      const haystack = norm(text);
      const ranges = [];
      for (const word of words) {
        for (let at = haystack.indexOf(word); at !== -1; at = haystack.indexOf(word, at + 1)) {
          ranges.push([at, at + word.length]);
        }
      }
      if (!ranges.length) continue;

      ranges.sort((a, b) => a[0] - b[0]);
      const merged = [ranges[0]];
      for (const [start, end] of ranges.slice(1)) {
        const last = merged[merged.length - 1];
        if (start <= last[1]) last[1] = Math.max(last[1], end);
        else merged.push([start, end]);
      }

      const fragment = document.createDocumentFragment();
      let cursor = 0;
      for (const [start, end] of merged) {
        fragment.append(text.slice(cursor, start), el("mark", { text: text.slice(start, end) }));
        cursor = end;
      }
      fragment.append(text.slice(cursor));
      node.replaceWith(fragment);
    }
  };

  const renderItem = (item, words) => {
    const cmds = el("div", { class: "ref-cmds" });
    for (const command of item.cmds) {
      cmds.append(el("button", { class: "chip", type: "button", "data-cmd": command, text: command }));
    }
    if (item.qual) cmds.append(el("span", { class: "qual", text: item.qual }));

    const node = el("div", { class: "ref-item" }, [cmds]);
    if (item.desc) node.append(el("p", { class: "ref-desc", html: item.desc }));
    highlight(node, words);
    return node;
  };

  const renderGroup = (section, group, items, words) => {
    const node = el("article", { class: "ref-group" });
    // «Асновы» внутри раздела «Асновы» — второй раз заголовок не нужен.
    if (group.hay !== section.hay) {
      node.append(el("h4", {}, [
        ...(group.emoji ? [el("span", { "aria-hidden": "true", text: group.emoji })] : []),
        group.title,
      ]));
    }
    if (!words.length) {
      for (const note of group.notes) node.append(el("p", { class: "ref-note", html: note }));
    }
    for (const item of items) node.append(renderItem(item, words));
    return node;
  };

  const tabButtons = new Map();
  for (const section of sections) {
    const button = el("button", { class: "tab", type: "button", text: section.title });
    button.addEventListener("click", () => {
      $(`#ref-${section.key}`)?.scrollIntoView({ block: "start" });
    });
    tabButtons.set(section.key, button);
    tabs.append(button);
  }

  const matchesAll = (haystack, words) => words.every((word) => haystack.includes(word));

  const sectionObserver = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const key = entry.target.dataset.key;
          tabButtons.forEach((button, buttonKey) => {
            button.setAttribute("aria-current", String(buttonKey === key));
          });
          // На телефоне лента вкладок шире экрана: докручиваем её до текущей.
          const active = tabButtons.get(key);
          tabs.scrollTo({ left: active.offsetLeft - (tabs.clientWidth - active.offsetWidth) / 2 });
        }
      }, { rootMargin: "-45% 0px -50% 0px" })
    : null;

  const render = (query) => {
    const words = norm(query).split(/\s+/).filter(Boolean);
    const nodes = [];
    let shown = 0;

    sectionObserver?.disconnect();

    for (const section of sections) {
      const sectionHit = words.length > 0 && matchesAll(section.hay, words);
      const groups = [];
      let sectionShown = 0;

      for (const group of section.groups) {
        const groupHit = sectionHit || (words.length > 0 && matchesAll(group.hay, words));
        const items = group.items.filter((item) => !words.length || groupHit || matchesAll(item.hay, words));
        if (!items.length) continue;
        groups.push(renderGroup(section, group, items, words));
        sectionShown += items.length;
      }

      tabButtons.get(section.key).disabled = sectionShown === 0;
      if (!sectionShown) continue;
      shown += sectionShown;

      const heading = el("h3", {}, [section.title, el("small", { text: String(sectionShown) })]);
      const node = el("section", { class: "ref-section", id: `ref-${section.key}`, "data-key": section.key }, [
        heading,
        el("div", { class: "ref-groups" }, groups),
      ]);
      nodes.push(node);
      sectionObserver?.observe(node);
    }

    if (!nodes.length) {
      nodes.push(el("p", {
        class: "ref-empty",
        text: "Ничо не нашлось. Может, такой команды нет. Может, ты не так пишешь. Скорее второе.",
      }));
    }

    refBody.replaceChildren(...nodes);
    refCount.textContent = words.length
      ? `нашлось строк: ${shown}`
      : `${allCommands.size} ${commandsNoun(allCommands.size)} в ${sections.length} разделах`;
  };

  // После фильтрации страница становится короче, и результаты уезжают под
  // липкую строку поиска. Подтягиваем их обратно.
  const keepResultsInView = () => {
    const toolsBottom = $(".ref-tools").getBoundingClientRect().bottom;
    const countTop = refCount.getBoundingClientRect().top;
    if (countTop < toolsBottom) window.scrollBy({ top: countTop - toolsBottom - 8 });
  };

  search.addEventListener("input", () => {
    render(search.value.trim());
    keepResultsInView();
  });
  render("");
})();
