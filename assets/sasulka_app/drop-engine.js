const IceDrop = (() => {
  const width = 400;
  const height = 500;
  const road = 448;
  const levels = [
    {
      id: 'record', name: 'Корниз', title: 'Ещё каплю?',
      instruction: 'Вырасти длинную сасульку и сними её, пока крепление держит.',
      target: 'Сними до обрыва', anchor: [200, 82], speed: 21, strength: 1, color: 'blue',
    },
    {
      id: 'bell', name: 'Званок', title: 'Дёрни званок',
      instruction: 'Удерживай и веди в сторону. Коснись колокольчика кончиком льда.',
      target: 'Коснись званка', anchor: [158, 95], speed: 34, strength: 1.15, color: 'amber',
      point: { x: 280, y: 266, r: 20 },
    },
    {
      id: 'branches', name: 'Ведки', title: 'Между ветками',
      instruction: 'Протяни лёд к синей ленте. Ветка остановит рост при касании.',
      target: 'Достигни синей ленты', anchor: [173, 86], speed: 33, strength: 1.35, color: 'blue',
      point: { x: 240, y: 405, r: 20 },
      branches: [
        { a: [0, 227], b: [105, 232], r: 10 },
        { a: [105, 232], b: [231, 265], r: 7 },
        { a: [136, 240], b: [180, 201], r: 5 },
        { a: [180, 201], b: [202, 193], r: 3 },
        { a: [400, 316], b: [326, 315], r: 9 },
        { a: [326, 315], b: [290, 345], r: 7 },
        { a: [337, 316], b: [316, 282], r: 4 },
      ],
    },
    {
      id: 'bridge', name: 'Дама', title: 'От дома к дому',
      instruction: 'Выведи кончик льда к кольцу на стене соседнего дома.',
      target: 'Соедини два дома', anchor: [94, 99], speed: 37, strength: 1.56, color: 'amber',
      point: { x: 308, y: 287, r: 19 },
    },
    {
      id: 'asphalt', name: 'Асвальт', title: 'До самого асфальта',
      instruction: 'Дотянись до светлой отметки на дороге. Пережди порыв, если лёд сильно качается.',
      target: 'Коснись отметки на дороге', anchor: [198, 81], speed: 28, strength: 1.34, color: 'blue',
      strip: [137, 261],
    },
  ];
  levels.push(
    { ...levels[1], id: 'lantern', name: 'Фанарь', title: 'Зимний фанарь', target: 'Коснись фанаря' },
    { ...levels[2], id: 'arch', name: 'Арко', title: 'Под ледяной аркой', target: 'Достигни ленты', instruction: 'Проведи лёд змейкой до синей ленты.' },
    { ...levels[3], id: 'alley', name: 'Пириулок', title: 'Узкий переулок', target: 'Дотянись до кольца', instruction: 'Обогни ветки в переулке и дотянись до соседнего дома.' },
    { id: 'mouth', name: 'Род', title: 'Пасасеш, ок?', target: 'Дотянись до рта', speed: 35, color: 'amber' },
  );
  const difficulties = {
    easy: { gates: 2, gap: 82, offset: 69, radius: 16, wind: .84, hold: 2.16, points: 30 },
    normal: { gates: 3, gap: 61, offset: 88, radius: 11, wind: 1, hold: 2.04, points: 70 },
    hard: { gates: 4, gap: 43, offset: 82, radius: 8, wind: 1.1, hold: 2.48, points: 120 },
  };
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const cm = length => Math.round(length * .6);
  const step = 1 / 60;
  const maxTicks = 4500;
  const themes = {
    bell: { objects: ['beam', 'iron'], route: 'Обойди деревянные балки и кованые карнизы.' },
    branches: { objects: ['birch', 'pine'], route: 'Обойди заснеженные ветки берёзы и ели.' },
    bridge: { objects: ['awning', 'beam'], route: 'Проведи лёд между навесами двух домов.' },
    asphalt: { objects: ['barrier', 'pipe'], route: 'Обойди дорожные ограждения и трубы.' },
    lantern: { objects: ['iron', 'awning'], route: 'Проведи лёд между решётками и навесами.' },
    arch: { objects: ['stone'], route: 'Пройди под каменной аркой, огибая её заснеженные уступы.' },
    alley: { objects: ['pipe', 'awning'], route: 'Лавируй между трубами и навесами в узком переулке.' },
    mouth: { objects: ['beam', 'pipe'], route: 'Обогни балки и трубы.' },
  };

  function course(id, difficulty = 'normal', seed = 0) {
    const base = levels.find(item => item.id === id);
    if (!base || !Object.hasOwn(difficulties, difficulty) || !Number.isInteger(seed) || seed < 0 || seed > 999999999) return null;
    if (id === 'record') return { ...base, difficulty: 'normal', seed: 0 };
    const rule = difficulties[difficulty];
    const { objects, route } = themes[id];
    const kind = { lantern: 'bell', arch: 'branches', alley: 'bridge' }[id] || id;
    let randomState = (seed + (levels.indexOf(base) * 8461)) >>> 0;
    function random() {
      randomState = (Math.imul(1664525, randomState) + 1013904223) >>> 0;
      return randomState / 4294967296;
    }
    const targetX = kind === 'bridge' ? 308 : Math.round(166 + random() * 103);
    const targetY = kind === 'asphalt' ? road : 423;
    const gates = [];
    const branches = [];
    let center = targetX;
    let direction = center > 208 ? -1 : 1;
    for (let i = rule.gates - 1; i >= 0; i--) {
      const shift = rule.offset + Math.round(random() * 16);
      if (center + direction * shift < 54 || center + direction * shift > 324) direction *= -1;
      center += direction * shift;
      direction *= -1;
      const y = 164 + Math.round(i * (targetY - 234) / (rule.gates - 1));
      const left = center - rule.gap / 2;
      const right = center + rule.gap / 2;
      branches.push(
        { a: [0, y], b: [left, y], r: 5, object: objects[i % objects.length] },
        { a: [400, y], b: [right, y], r: 5, object: objects[(i + 1) % objects.length] },
      );
      gates.unshift({ x: center, y });
    }
    const rootX = clamp(gates[0].x + direction * 73, 70, 305);
    return {
      ...base, kind, seed, difficulty, gates, branches, anchor: [rootX, 85], strength: rule.hold,
      instruction: route + ' ' + base.target + '.',
      windScale: rule.wind, windOffset: seed ? random() * 5 : 0,
      point: kind === 'asphalt' ? null : { x: targetX, y: targetY, r: rule.radius },
      strip: kind === 'asphalt' ? [targetX - rule.radius, targetX + rule.radius] : null,
    };
  }
  const gusts = [[7.5, 2.1, .84], [14.6, 2.55, .97], [22, 2.35, .9], [28, 1.85, .75]];

  function windAt(time) {
    const phase = time % 32;
    let force = .12 + .04 * Math.sin(time * 1.9);
    let warning = false;
    for (const [peak, radius, strength] of gusts) {
      const distance = Math.abs(phase - peak);
      if (distance < radius) force += strength * (.5 + .5 * Math.cos(distance * Math.PI / radius));
      if (phase >= peak - radius - 1.25 && phase < peak) warning = true;
    }
    return { force: clamp(force, 0, 1.1), warning };
  }

  function start(level, walkerOffset = 0) {
    const [x, rootY] = level.anchor;
    const initial = 14;
    return {
      time: 0, x, y: rootY + initial, length: initial, points: [level.anchor],
      load: 0, cracks: 0, wind: windAt(0), walkerOffset, status: 'ready', reason: null,
    };
  }

  function distanceFromBranch(x, y, { a: [ax, ay], b: [bx, by] }) {
    const dx = bx - ax;
    const dy = by - ay;
    const t = clamp(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0, 1);
    return Math.hypot(x - ax - t * dx, y - ay - t * dy);
  }

  function update(state, level, { pour, steer }, seconds) {
    if (state.status === 'ready' && !pour) return;
    if (state.status !== 'ready' && state.status !== 'playing') return;
    state.status = 'playing';
    state.time += seconds;
    state.wind = windAt(state.time);
    if (level.windScale) {
      state.wind = windAt(state.time + level.windOffset);
      state.wind.force *= level.windScale;
    }

    if (pour && state.y < road) {
      const direction = level.id === 'record' ? 0 : clamp(steer, -1, 1);
      const speed = level.speed * seconds;
      const x = clamp(state.x + speed * direction * 1.18, 14, width - 14);
      const y = Math.min(road, state.y + speed * (1 - .31 * Math.abs(direction)));
      state.length += Math.hypot(x - state.x, y - state.y);
      state.x = x;
      state.y = y;
      const last = state.points[state.points.length - 1];
      if (Math.hypot(x - last[0], y - last[1]) >= 4) state.points.push([x, y]);
    }

    const lean = Math.abs(state.x - level.anchor[0]);
    state.load = (state.length * (.132 + state.wind.force * .225) + lean * .08) / level.strength;
    state.cracks = Math.max(0, state.cracks + seconds * (state.load >= 79 ? (state.load - 79) * .32 : -.17));
    const obstacle = level.branches?.find(branch => distanceFromBranch(state.x, state.y, branch) <= branch.r + 4);
    if (state.load >= 113 || state.cracks >= 16) {
      state.status = 'broken';
      state.reason = 'load';
    } else if (obstacle) {
      state.status = 'missed';
      state.reason = 'obstacle';
      state.obstacle = obstacle.object;
    } else if (level.point && Math.hypot(state.x - level.point.x, state.y - level.point.y) <= level.point.r) {
      state.status = 'won';
    } else if (level.strip && state.y === road && state.x >= level.strip[0] && state.x <= level.strip[1]) {
      state.status = 'won';
    } else if ((level.point && state.y > level.point.y + level.point.r) || (level.strip && state.y === road)) {
      state.status = 'missed';
      state.reason = 'target';
    }
    if (state.status === 'playing' && state.time >= maxTicks * step) {
      state.status = 'missed';
      state.reason = 'time';
    }
  }

  function canHarvest(state, level) {
    return level.id === 'record' && state.status === 'playing' && state.length >= 30;
  }

  function harvest(state, level) {
    if (!canHarvest(state, level)) return false;
    state.status = 'harvested';
    return true;
  }

  function walkerAt(time, offset) {
    const clock = time + offset;
    const period = 11;
    const age = clock % period;
    const duration = 7;
    if (age > duration) return null;
    const direction = Math.floor(clock / period) % 2 === 0 ? 1 : -1;
    const distance = age / duration * (width + 44) - 22;
    return { x: direction === 1 ? distance : width - distance, direction, age };
  }

  function fallOutcome(state, level, random = Math.random) {
    const impactAt = .78;
    const x = clamp(state.x + 11, 18, width - 18);
    const walker = level.id === 'record' ? walkerAt(state.time + impactAt, state.walkerOffset) : null;
    const cameo = Boolean(walker && x >= walker.x - 9 && x <= walker.x + 13 && random() < .08);
    return { x, impactAt, cameo };
  }

  function replay(level, commands) {
    const state = start(level);
    let ticks = 0;
    if (!Array.isArray(commands) || !commands.length || commands.length > maxTicks) return null;
    for (let i = 0; i < commands.length; i++) {
      const command = commands[i];
      if (!Array.isArray(command) || command.length !== 3) return null;
      const [pour, direction, count] = command;
      if ((pour !== 0 && pour !== 1) || (!i && pour !== 1) || !Number.isInteger(direction) || Math.abs(direction) > 100 ||
          !Number.isInteger(count) || count < 1 || (ticks += count) > maxTicks) return null;
      for (let j = 0; j < count; j++) {
        if (state.status !== 'ready' && state.status !== 'playing') return null;
        update(state, level, { pour: Boolean(pour), steer: direction / 100 }, step);
      }
    }
    if (level.id === 'record') harvest(state, level);
    return state.status === 'won' || state.status === 'harvested' ? state : null;
  }

  return { width, height, road, levels, difficulties, course, step, maxTicks, replay, cm, start, update, canHarvest, harvest, walkerAt, fallOutcome };
})();

if (typeof module !== 'undefined') module.exports = IceDrop;
