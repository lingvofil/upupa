(() => {
  const byId = id => document.getElementById(id);
  function text(id, value) {
    const element = byId(id);
    if (element.textContent !== value) element.textContent = value;
  }

  const canvas = byId('ice-view');
  const pourButton = byId('pour');
  const harvestButton = byId('harvest');
  const levels = IceDrop.levels;
  const saveKey = 'upupa:sasulka:drop:v1';
  const fixedStep = IceDrop.step;
  const keys = new Set();
  const query = new URLSearchParams(location.search);
  const startParam = window.Telegram?.WebApp?.initDataUnsafe?.start_param || query.get('tgWebAppStartParam');
  const [requested, requestedDifficulty, requestedSeed] =
    (startParam?.startsWith('drop_') ? startParam.slice(5) : query.get('level') || '').split('-');
  const scene = createDropScene(canvas, draw);
  let progress = readProgress();
  let level;
  let state;
  let trace;
  let pointer = null;
  let fall = null;
  let celebration = 0;
  let telegram;
  let audio;
  let frame = 0;
  let previousFrame = 0;
  let accumulator = 0;

  const buttons = levels.map(item => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = item.name;
    button.addEventListener('click', () => {
      if (item.id !== level.id || level.seed) select(item);
    });
    byId('roofs').append(button);
    return button;
  });

  function readProgress() {
    try {
      const saved = JSON.parse(localStorage.getItem(saveKey));
      const record = Number.isFinite(saved?.record) && saved.record >= 0 ? saved.record : 0;
      const won = levels.filter(item => Array.isArray(saved?.won) && saved.won.includes(item.id)).map(item => item.id);
      return { record, won, completed: Array.isArray(saved?.completed) ? saved.completed : [],
        selected: saved?.selected, difficulty: saved?.difficulty };
    } catch {
      return { record: 0, won: [], completed: [] };
    }
  }

  function save() {
    const stored = readProgress();
    progress.record = Math.max(progress.record, stored.record);
    progress.won = [...new Set([...progress.won, ...stored.won])];
    progress.completed = [...new Set([...progress.completed, ...stored.completed])];
    try {
      localStorage.setItem(saveKey, JSON.stringify(progress));
    } catch {
      // The current session still retains results when storage is unavailable.
    }
  }

  function initTelegram() {
    const webApp = window.Telegram?.WebApp;
    if (!webApp || telegram === webApp) return;
    telegram = webApp;
    webApp.ready();
    webApp.expand();
    webApp.onEvent?.('deactivated', suspend);
    webApp.onEvent?.('activated', resume);
  }

  function setDragLock(locked) {
    if (!telegram?.initData || !telegram.isVersionAtLeast?.('7.7')) return;
    if (locked) telegram.disableVerticalSwipes();
    else telegram.enableVerticalSwipes();
  }

  function releasePointer() {
    if (!pointer) return;
    const { surface, id, touch } = pointer;
    pointer = null;
    if (surface.hasPointerCapture(id)) surface.releasePointerCapture(id);
    if (touch) setDragLock(false);
  }

  function clearInput() {
    releasePointer();
    keys.clear();
  }

  function suspend() {
    clearInput();
    previousFrame = 0;
    accumulator = 0;
    if (state) renderHud();
  }

  function select(item, seed = 0) {
    level = IceDrop.course(item.id, byId('difficulty').value, seed);
    progress.selected = item.id;
    save();
    byId('challenge-title').textContent = level.title;
    byId('instructions').textContent = level.instruction;
    byId('goal').textContent = level.target;
    byId('record-line').hidden = level.id !== 'record';
    harvestButton.hidden = level.id !== 'record';
    byId('keyboard-extra').textContent = level.id === 'record' ? 'Enter — снять' : '← → — направлять лёд';
    byId('control-hint').textContent = level.id === 'record' ?
      'Палец можно держать прямо на поле.' : 'Удерживай на поле или веди вбок саму кнопку.';
    reset();
  }

  function reset() {
    suspend();
    state = IceDrop.start(level, Math.random() * 22);
    trace = [];
    fall = null;
    celebration = 0;
    byId('active').hidden = false;
    byId('finish').hidden = true;
    byId('cameo').hidden = true;
    byId('share-note').textContent = '';
    byId('share-text').hidden = true;
    DropRanking.clearStatus();
    updateTabs();
    renderHud();
    draw();
  }

  function updateTabs() {
    byId('record').textContent = progress.record ? progress.record + ' см' : '—';
    const options = Object.keys(IceDrop.difficulties);
    const completedCount = id => options.filter(option => progress.completed.includes(id + '-' + option)).length;
    buttons.forEach((button, index) => {
      const item = levels[index];
      button.setAttribute('aria-pressed', String(item.id === level.id && !level.seed));
      const count = completedCount(item.id);
      const done = item.id !== 'record' && count === options.length;
      button.dataset.done = String(done);
      button.setAttribute('aria-label', done ? item.name + ', пройдены все сложности' : item.name);
    });
    byId('course-progress').hidden = level.id === 'record' || Boolean(level.seed);
    text('course-progress', 'Пройдено ' + completedCount(level.id) + ' из ' + options.length + ' сложностей');
  }

  function running() {
    return state.status === 'ready' || state.status === 'playing';
  }

  function input() {
    const keySteer = Number(keys.has('ArrowRight')) - Number(keys.has('ArrowLeft'));
    return {
      pour: Boolean(pointer || keys.has('Space')),
      steer: pointer ? (pointer.aim - state.x) / 38 : keySteer,
    };
  }

  function renderHud() {
    const critical = state.load >= 95 || state.cracks >= 7;
    const watch = state.load >= 70 || state.cracks >= 1;
    const strongWind = state.wind.force >= .59;
    const wind = byId('wind');
    text('length', IceDrop.cm(state.length) + ' см');
    text('wind', strongWind ? 'Шквал' : state.wind.warning ? 'Усиливается' : 'Тихо');
    wind.parentElement.dataset.gust = String(strongWind || state.wind.warning);
    byId('strain').dataset.risk = critical ? 'critical' : watch ? 'watch' : 'steady';
    text('strain-label', critical ? 'Трещит' : watch ? 'Качается' : 'Держится');
    byId('strain-bar').style.width = Math.min(100, Math.round(state.load)) + '%';
    harvestButton.disabled = !IceDrop.canHarvest(state, level);
    byId('reset').hidden = state.status === 'ready';
    const pouring = input().pour;
    pourButton.dataset.pouring = String(pouring);
    text('pour', pouring ? 'Лёд растёт…' : 'Удерживай, чтобы растить');
  }

  function draw() {
    if (!state) return;
    scene.draw(level, state, {
      pouring: running() && input().pour,
      aim: pointer?.aim ?? null, fall, motion: state.time + celebration,
    });
  }

  function schedule() {
    if (!frame) frame = requestAnimationFrame(tick);
  }

  function tick(time) {
    frame = 0;
    if (document.hidden || DropRanking.dialog.open || (telegram?.initData && telegram.isActive === false)) {
      previousFrame = 0;
      return;
    }
    const elapsed = previousFrame ? Math.min((time - previousFrame) / 1000, .1) : 0;
    previousFrame = time;
    if (running()) {
      const command = input();
      const direction = Math.round(Math.max(-1, Math.min(1, command.steer)) * 100);
      const pour = Number(command.pour);
      accumulator += elapsed;
      while (accumulator >= fixedStep && running()) {
        if (pour || state.status === 'playing') {
          const last = trace.at(-1);
          if (last && last[0] === pour && last[1] === direction) last[2]++;
          else trace.push([pour, direction, 1]);
        }
        IceDrop.update(state, level, { pour: Boolean(pour), steer: direction / 100 }, fixedStep);
        accumulator -= fixedStep;
      }
      if (!running()) endRun();
      else renderHud();
    } else {
      celebration += elapsed;
      if (fall) {
        fall.elapsed += elapsed;
        if (fall.cameo && fall.elapsed >= fall.impactAt) byId('cameo').hidden = false;
      }
    }
    draw();
    if (state.status === 'playing' || (state.status === 'ready' && input().pour) ||
        (fall && fall.elapsed < 1.34) || (state.status === 'won' && celebration < 1.05)) {
      schedule();
    } else {
      previousFrame = 0;
    }
  }

  function resume() {
    previousFrame = 0;
    if (state) schedule();
  }

  function resultFor() {
    const length = IceDrop.cm(state.length) + ' см';
    if (state.status === 'harvested') {
      const record = IceDrop.cm(state.length) > progress.record;
      return [record ? 'Новый рекорд' : 'Сасулька снята', length + ' льда удалось сохранить.'];
    }
    if (state.status === 'broken') {
      return ['Сасулька сорвалась', 'Крепление не выдержало. Перед обрывом было ' + length + '.'];
    }
    if (state.status === 'missed') {
      if (state.reason === 'time') return ['Время вышло', 'Попробуй пройти трассу быстрее.'];
      if (state.reason === 'obstacle') {
        const names = {
          birch: 'ветку берёзы', pine: 'ветку ели', stone: 'каменный уступ', awning: 'навес',
          pipe: 'трубу', beam: 'балку', iron: 'решётку', barrier: 'ограждение',
        };
        return ['Лёд задел ' + names[state.obstacle], 'Попробуй повести кончик через просвет.'];
      }
      return ['Лёд прошёл мимо', 'Начни поворачивать к отметке выше по пути.'];
    }
    const titles = {
      bell: 'Званок звенит', branches: 'Лента достигнута',
      bridge: 'Дома соединены', asphalt: 'Лёд коснулся асфальта', lantern: 'Фанарь достигнут',
      arch: 'Лента достигнута', alley: 'Дома соединены', mouth: 'Сасулька во рту!'
    };
    return [titles[level.id], 'Испытание пройдено. Длина ' + length + '.'];
  }

  function quipFor() {
    const groups = {
      harvested: ['Нихуя себе оглобля.', 'Забирай, пока не спиздили.', 'Заверните, я за этим и пришол.'],
      broken: ['Пиздец креплению. Зато красиво.', 'Ёбнулась. Дворник передаёт привет.', 'Ну и хуле ты держал до последнего?'],
      missed: ['Мимо, бля. Глазомер замёрз?', 'Прямо в хуй знает куда.', 'Палец-то отогрей перед второй попыткой.'],
      won: ['Ебать ты ювелир.', 'Ну всё, лёд ахуел.', 'Вот это, бля, точность.'],
      mouth: ['Пасасал. К зубному сам.', 'Охуеть, попал. Губы прилипли.', 'Вот и всё, теперь губы отморозил.'],
    };
    const quips = groups[state.status === 'won' && level.id === 'mouth' ? 'mouth' : state.status];
    return quips[Math.floor(Math.random() * quips.length)];
  }

  function endRun() {
    clearInput();
    const [title, detail] = resultFor();
    const success = state.status === 'won' || state.status === 'harvested';
    if (state.status === 'harvested') progress.record = Math.max(progress.record, IceDrop.cm(state.length));
    if (state.status === 'won' && !level.seed) progress.completed.push(level.id + '-' + level.difficulty);
    if (success) save();
    if (success) DropRanking.submit({ id: level.id, difficulty: level.difficulty, seed: level.seed, trace });
    if (state.status === 'broken') fall = { ...IceDrop.fallOutcome(state, level), elapsed: 0 };
    if (state.status === 'won' && level.id === 'bell') ring();
    byId('active').hidden = true;
    byId('finish').hidden = false;
    byId('result-title').textContent = title;
    byId('result-detail').textContent = detail;
    byId('quip').textContent = quipFor();
    byId('next').hidden = state.status !== 'won' || (!level.seed && level.id === levels.at(-1).id);
    byId('share').hidden = false;
    renderHud();
    updateTabs();
    telegram?.HapticFeedback?.notificationOccurred(success ? 'success' : 'warning');
    byId('result-title').focus({ preventScroll: true });
  }

  function unlockBell() {
    if (level.id !== 'bell') return;
    const Audio = window.AudioContext ?? window.webkitAudioContext;
    if (!Audio) return;
    try {
      audio ??= new Audio();
      if (audio.state === 'suspended') audio.resume().catch(() => {});
    } catch {
      // The bell still moves and marks the win when audio is unavailable.
    }
  }

  function ring() {
    if (audio?.state !== 'running') return;
    const now = audio.currentTime;
    for (const [pitch, delay, volume] of [[784, 0, .10], [1190, .021, .036], [1568, .041, .022]]) {
      const tone = audio.createOscillator();
      const fade = audio.createGain();
      tone.frequency.value = pitch;
      fade.gain.setValueAtTime(volume, now + delay);
      fade.gain.exponentialRampToValueAtTime(.001, now + delay + .55);
      tone.connect(fade).connect(audio.destination);
      tone.start(now + delay);
      tone.stop(now + delay + .56);
    }
  }

  function aimAt(clientX, surface, origin) {
    if (surface === canvas) {
      const rect = canvas.getBoundingClientRect();
      return (clientX - rect.left) / rect.width * IceDrop.width;
    }
    return origin.x + (clientX - origin.clientX) * 2.4;
  }

  function startPointer(event) {
    if (!running() || pointer || event.button !== 0) return;
    event.preventDefault();
    const surface = event.currentTarget;
    const origin = { x: state.x, clientX: event.clientX };
    const touch = event.pointerType !== 'mouse';
    pointer = { id: event.pointerId, surface, origin, aim: aimAt(event.clientX, surface, origin), touch };
    surface.setPointerCapture(event.pointerId);
    if (touch) setDragLock(true);
    unlockBell();
    renderHud();
    schedule();
  }

  function movePointer(event) {
    if (event.pointerId !== pointer?.id) return;
    pointer.aim = aimAt(event.clientX, pointer.surface, pointer.origin);
  }

  function stopPointer(event) {
    if (event.pointerId !== pointer?.id) return;
    releasePointer();
    renderHud();
    draw();
  }

  function onKeyDown(event) {
    if (!['Space', 'Enter', 'ArrowLeft', 'ArrowRight'].includes(event.code) || !running()) return;
    const target = event.target;
    if (target !== pourButton && target instanceof Element && target.closest('button, a, input, textarea')) return;
    event.preventDefault();
    if (event.code === 'Enter') {
      if (!event.repeat) harvest();
    } else {
      keys.add(event.code);
      if (event.code === 'Space' && !event.repeat) unlockBell();
      schedule();
    }
  }

  function harvest() {
    if (!IceDrop.harvest(state, level)) return;
    endRun();
    draw();
  }

  function challengeLink() {
    const configured = document.querySelector('meta[name="telegram-mini-app"]').content.trim();
    const page = new URL(location.href);
    const code = [level.id, level.difficulty, level.seed].join('-');
    if (configured) {
      const link = new URL(configured, page);
      if (link.hostname === 't.me' && link.protocol === 'https:') {
        link.searchParams.set('startapp', 'drop_' + code);
        return link.href;
      }
    }
    if (page.protocol !== 'https:') return '';
    page.hash = '';
    page.search = '';
    page.searchParams.set('view', 'sasulka');
    page.searchParams.set('level', code);
    return page.href;
  }

  async function share() {
    const result = state.status === 'harvested' ? 'Я снял сасульку на ' + IceDrop.cm(state.length) + ' см.' :
      state.status === 'won' ? 'Я прошёл «' + level.title + '».' : 'Попробуй «' + level.title + '».';
    const text = result + ' Сыграем на той же крыше?';
    const link = challengeLink();
    if (telegram?.initData && link) {
      const params = new URLSearchParams({ url: link, text });
      telegram.openTelegramLink('https://t.me/share/url?' + params);
      return;
    }
    if (navigator.share) {
      try {
        await navigator.share(link ? { title: 'Сасулька', text, url: link } : { title: 'Сасулька', text });
        return;
      } catch (error) {
        if (error.name === 'AbortError') return;
      }
    }
    const message = link ? text + '\n' + link : text;
    try {
      await navigator.clipboard.writeText(message);
      byId('share-note').textContent = 'Вызов скопирован.';
    } catch {
      const field = byId('share-text');
      field.value = message;
      field.hidden = false;
      field.select();
      byId('share-note').textContent = 'Скопируй выделенный текст.';
    }
  }

  [canvas, pourButton].forEach(surface => {
    surface.addEventListener('pointerdown', startPointer);
    surface.addEventListener('pointermove', movePointer);
    surface.addEventListener('pointerup', stopPointer);
    surface.addEventListener('pointercancel', stopPointer);
    surface.addEventListener('lostpointercapture', stopPointer);
    surface.addEventListener('contextmenu', event => event.preventDefault());
  });
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', event => keys.delete(event.code));
  window.addEventListener('blur', suspend);
  document.addEventListener('visibilitychange', () => { if (document.hidden) suspend(); else resume(); });
  byId('telegram-sdk').addEventListener('load', initTelegram);
  harvestButton.addEventListener('click', harvest);
  byId('reset').addEventListener('click', reset);
  byId('retry').addEventListener('click', () => { reset(); pourButton.focus({ preventScroll: true }); });
  function randomCourse() {
    const seed = Math.floor(Math.random() * 999999999) + 1;
    select(levels[1 + seed % (levels.length - 1)], seed);
  }

  byId('next').addEventListener('click', () => {
    if (level.seed) randomCourse();
    else select(levels[levels.findIndex(item => item.id === level.id) + 1]);
    pourButton.focus({ preventScroll: true });
  });
  byId('random').addEventListener('click', randomCourse);
  byId('difficulty').addEventListener('change', event => {
    progress.difficulty = event.target.value;
    if (level.id === 'record') { save(); updateTabs(); }
    else select(level, level.seed);
  });
  DropRanking.dialog.addEventListener('close', resume);
  byId('share').addEventListener('click', share);
  const difficulty = requestedDifficulty || progress.difficulty;
  if (Object.hasOwn(IceDrop.difficulties, difficulty)) byId('difficulty').value = difficulty;
  const seed = Number(requestedSeed);
  select(levels.find(item => item.id === requested) ?? levels.find(item => item.id === progress.selected) ?? levels[0],
    Number.isInteger(seed) && seed >= 0 && seed <= 999999999 ? seed : 0);
  initTelegram();
})();
