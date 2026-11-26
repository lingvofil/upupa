function createDropScene(canvas, redraw) {
  const { width, height, road, walkerAt } = IceDrop;
  const ctx = canvas.getContext('2d');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const background = new Image();
  background.addEventListener('load', redraw);
  background.src = '?view=sasulka&asset=village.webp';
  new ResizeObserver(redraw).observe(canvas);
  const mix = (a, b, t) => a + (b - a) * t;
  const seed = i => {
    const value = Math.sin(i * 131.7 + 71) * 47832.176;
    return value - Math.floor(value);
  };

  function path(points) {
    ctx.beginPath();
    for (let i = 0; i < points.length; i++) {
      const [x, y] = points[i];
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
  }

  function poly(points, color) {
    path(points);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
  }

  function line(points, color, size) {
    path(points);
    ctx.strokeStyle = color;
    ctx.lineWidth = size;
    ctx.stroke();
  }

  function circle(x, y, r, color) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }

  function backdrop(level, state) {
    ctx.fillStyle = '#223b58';
    ctx.fillRect(0, 0, width, height);
    if (background.naturalWidth) {
      const fit = Math.max(width / background.naturalWidth, height / background.naturalHeight);
      const w = background.naturalWidth * fit;
      const h = background.naturalHeight * fit;
      ctx.drawImage(background, (width - w) * .54, (height - h) * .6, w, h);
    }
    ctx.fillStyle = level.color === 'amber' ? '#25184736' : '#061c3980';
    ctx.fillRect(0, 0, width, height);
    const drift = reducedMotion.matches ? 0 : state.time + (state.wind.force > .4 ? state.time * .4 : 0);
    for (let i = 0; i < 37; i++) {
      const x = (seed(i + 18) * width + drift * (15 + seed(i) * 8)) % width;
      const y = (seed(i + 93) * height + drift * (12 + seed(i) * 15)) % height;
      if (state.wind.force >= .6 && !reducedMotion.matches) {
        line([[x, y], [x + 8, y + 1]], '#cceaff95', 1);
      } else {
        circle(x, y, .6 + seed(i + 1) * 1.2, '#dbf1fa84');
      }
    }
  }

  function street(level) {
    poly([[0, road - 5], [65, road - 3], [142, road - 6], [263, road - 3], [400, road - 8],
      [400, height], [0, height]], '#1a2738');
    line([[0, road - 3], [105, road - 3], [150, road - 5], [258, road], [400, road - 6]], '#a8c4d8', 4);
    for (let i = 0; i < 38; i++) {
      const x = seed(i + 181) * width;
      const y = road + seed(i + 22) * (height - road);
      line([[x, y], [x + seed(i + 5) * 8 + 2, y]], '#6c819044', 1);
    }
    if (level.strip) {
      const [left, right] = level.strip;
      ctx.setLineDash([8, 5]);
      line([[left, road + 1], [right, road + 1]], '#aafdff', 3);
      ctx.setLineDash([]);
      line([[left, road - 6], [left, road + 6]], '#aafdff', 2);
      line([[right, road - 6], [right, road + 6]], '#aafdff', 2);
    }
  }

  function roof(level) {
    const [x, y] = level.anchor;
    const end = level.kind === 'bridge' ? x + 70 : Math.min(379, x + 125);
    poly([[0, 0], [Math.min(140, x - 20), 0], [end, y - 26],
      [end - 6, y - 15], [x, y - 3], [0, y - 5]], '#162435');
    poly([[0, y - 24], [x * .33, y - 24], [x * .66, y - 14], [x, y],
      [end, y - 15], [end + 6, y - 27], [end - 15, y - 31], [x, y - 22],
      [x * .61, y - 30], [x * .21, y - 38], [0, y - 34]], '#afc9dc');
    line([[0, y - 18], [x * .48, y - 13], [x, y - 2], [end, y - 14]], '#425a71', 4);
    line([[x - 13, y + 2], [x + 12, y + 2]], '#b3e8f3', 2);
  }

  function house(right, alley) {
    ctx.save();
    if (!right) { ctx.translate(width, 0); ctx.scale(-1, 1); }
    const top = alley ? 119 : 177;
    poly([[328, top], [400, top - 30], [400, road], [328, road]], right ? '#293749' : '#433943');
    poly([[316, top], [400, top - 36], [400, top - 25], [326, top + 10]], '#94acc2');
    for (let i = 0; i < 5; i++) {
      const y = top + 29 + i * 49;
      poly([[345, y], [379, y - 5], [379, y + 21], [345, y + 25]], '#795837');
      poly([[349, y + 4], [375, y], [375, y + 18], [349, y + 21]], i % 2 ? '#78634e' : '#d09a56');
      line([[362, y + 2], [362, y + 20]], '#47363b', 2);
      line([[345, y + 12], [379, y + 8]], '#47363b', 2);
    }
    ctx.restore();
  }

  function arch() {
    const crown = 146;
    const base = 280;
    const outer = 172;
    const inner = 137;
    for (let i = 0; i < 13; i++) {
      const a = Math.PI + i * Math.PI / 13 + .012;
      const b = Math.PI + (i + 1) * Math.PI / 13 - .012;
      const edge = (angle, radius) => [200 + Math.cos(angle) * radius,
        base + Math.sin(angle) * (radius - outer + base - crown)];
      poly([edge(a, outer), edge(b, outer), edge(b, inner), edge(a, inner)],
        i % 2 ? '#66778d' : '#586b82');
      line([edge(a, outer), edge(b, outer)], '#9cafc4', 1.5);
    }
    for (const x of [28, 337]) {
      for (let y = base; y < road; y += 23) {
        poly([[x, y], [x + 35, y], [x + 35, y + 21], [x, y + 21]], '#586b82');
        line([[x + 3, y + 2], [x + 31, y + 2]], '#8396ac', 1);
      }
    }
  }

  function woodland() {
    poly([[0, 80], [22, 95], [27, 448], [0, 448]], '#777785');
    for (let y = 112; y < 440; y += 22) {
      line([[0, y], [10 + seed(y) * 12, y + 1]], '#343a47', 2);
    }
    poly([[394, 71], [400, 65], [400, 448], [373, 448]], '#393945');
    for (let y = 108; y < 416; y += 43) {
      poly([[395, y], [359, y + 35], [383, y + 30], [363, y + 48], [400, y + 41]], '#273e46');
    }
  }

  function obstacle({ a: [ax, y], b: [bx], object }) {
    ctx.save();
    ctx.translate(ax, y);
    ctx.scale(Math.sign(bx - ax), 1);
    const end = Math.abs(bx - ax);
    if (object === 'birch' || object === 'pine') {
      const birch = object === 'birch';
      const bark = birch ? '#c8c4c8' : '#78534c';
      for (let i = 1; i <= 4; i++) {
        const x = end * i / 6;
        const twig = Math.min(31, end * .12);
        const side = i % 2 ? -1 : 1;
        line([[x, 0], [x + twig * .48, side * twig * .44], [x + twig, side * twig]], birch ? '#7b7687' : '#567677', 2.3);
        if (!birch) poly([[x, 2], [x + twig, side * twig], [x + twig * .79, side * 4]], '#426864');
      }
      poly([[0, -7], [end * .43, -5], [end, -3], [end, 3], [end * .43, 5], [0, 8]], bark);
      line([[0, -5], [end * .6, -4], [end, -3]], '#d9e9ef', 2);
      if (birch) for (let x = 9; x < end - 6; x += 17) line([[x, -3], [x + 3, 1]], '#555565', 1.8);
    } else if (object === 'stone') {
      for (let x = 0; x < end; x += 19) {
        poly([[x, -6], [Math.min(end, x + 17), -6], [Math.min(end, x + 17), 6], [x, 6]], '#77879c');
        line([[x, -5], [Math.min(end, x + 17), -5]], '#d1e0ed', 2);
      }
    } else if (object === 'awning') {
      poly([[0, -7], [end * .83, -6], [end, -3], [end, 5], [0, 6]], '#ad6771');
      for (let x = 8; x < end - 5; x += 22) line([[x, -3], [x, 4]], '#d49592', 5);
      line([[0, -6], [end * .74, -6], [end, -4]], '#d8e7ef', 2.5);
    } else {
      const palette = {
        pipe: ['#557f88', '#9dc2c8'], iron: ['#333c50', '#a4c0d5'],
        beam: ['#96705b', '#dacab0'], barrier: ['#bd6556', '#ffe3b1'],
      };
      const [base, detail] = palette[object];
      if (object === 'pipe') {
        line([[14, -22], [14, -9], [19, 0]], base, 10);
        line([[14, -22], [14, -9], [19, 0]], detail, 2);
      }
      line([[0, 0], [end, 0]], base, 10);
      line([[0, -4], [end, -4]], detail, 1.7);
      for (let x = 12; x < end - 2; x += 24) {
        if (object === 'barrier') line([[x - 5, 3], [x + 1, -3]], detail, 3.5);
        else if (object === 'beam') circle(x, 1, 1.5, detail);
        else if (object === 'iron') {
          ctx.beginPath();
          ctx.arc(x, 0, 3, 0, Math.PI * 1.68);
          ctx.strokeStyle = detail;
          ctx.lineWidth = 1.2;
          ctx.stroke();
        } else line([[x, -4], [x, 4]], detail, 2);
      }
    }
    ctx.restore();
  }

  function furniture(level, state, motion) {
    if (level.id === 'branches') woodland();
    if (level.id === 'arch') arch();
    if (level.kind === 'bridge') {
      if (level.id === 'alley') house(false, true);
      house(true, level.id === 'alley');
    }
    for (const item of level.branches ?? []) obstacle(item);

    if (!level.point) return;
    const { x, y, r } = level.point;
    const won = state.status === 'won';
    const gold = '#ffcf8c';
    const highlight = level.color === 'amber' ? gold : '#9ae7ff';
    if (won) circle(x, y, r + 8 + 6 * Math.sin(motion * 5), '#c2faff40');
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.strokeStyle = highlight;
    ctx.lineWidth = 1.35;
    ctx.stroke();
    ctx.setLineDash([]);
    if (level.id === 'mouth') {
      drawMouth(x, y, r, won);
    } else if (level.id === 'lantern') {
      line([[x, y - 21], [x, y - 10]], '#e5bd8d', 2);
      poly([[x - 8, y - 9], [x + 8, y - 9], [x + 6, y + 7], [x - 6, y + 7]], '#ffe0a6');
      line([[x, y - 9], [x, y + 7]], '#a86d48', 2);
    } else if (level.kind === 'bell') {
      const sway = won && !reducedMotion.matches ? Math.sin(motion * 16) * 3 : 0;
      line([[x, y - 34], [x + sway, y - 8]], '#e5bd8d', 1.5);
      circle(x + sway, y + 10, 3.8, gold);
      poly([[x - 4 + sway, y - 9], [x + 4 + sway, y - 9], [x + 9 + sway, y + 6],
        [x + 12 + sway, y + 8], [x - 12 + sway, y + 8], [x - 9 + sway, y + 6]], '#d69753');
      line([[x - 2 + sway, y - 8], [x - 5 + sway, y + 5]], '#ffe0a6', 2);
    } else if (level.kind === 'bridge') {
      line([[x + 9, y], [331, y]], '#bea388', 5);
      ctx.beginPath();
      ctx.arc(x, y, 11, 0, Math.PI * 2);
      ctx.strokeStyle = gold;
      ctx.lineWidth = 4;
      ctx.stroke();
    } else {
      line([[x - 4, y - 10], [x - 4, y + 20]], '#91edf8', 2);
      poly([[x - 3, y - 10], [x + 13, y - 7], [x + 8, y - 2], [x + 13, y + 4],
        [x - 3, y + 3]], '#6dd9f2');
    }
  }

  function drawMouth(x, y, radius, won) {
    poly([[x - 46, y + 77], [x - 36, y + 49], [x, y + 37], [x + 36, y + 49], [x + 46, y + 77]], '#744779');
    line([[x - 25, y + 47], [x + 23, y + 53]], '#e49a6e', 14);
    circle(x, y + 18, 32, '#e5aa88');
    circle(x - 28, y + 19, 7, '#da9a7f');
    circle(x + 28, y + 19, 7, '#da9a7f');
    ctx.beginPath();
    ctx.ellipse(x, y, Math.max(radius, 12), Math.max(radius * .75, 8), 0, 0, Math.PI * 2);
    ctx.fillStyle = won ? '#6a2847' : '#411e32';
    ctx.fill();
    ctx.strokeStyle = '#f58385';
    ctx.lineWidth = 3;
    ctx.stroke();
    line([[x - 10, y + 35], [x - 6, y + 32]], '#6c4645', 3);
    line([[x + 6, y + 32], [x + 10, y + 35]], '#6c4645', 3);
  }

  function renderWalker(walker, hit) {
    const x = walker.x;
    const y = road + 13;
    const step = Math.sin(walker.age * 15) * 4;
    line([[x, y - 6], [x - 3 + step, y + 3]], '#243244', 3);
    line([[x + 1, y - 7], [x + 4 - step, y + 3]], '#243244', 3);
    line([[x, y - 17], [x, y - 6]], '#d3a485', 7);
    circle(x, y - 21, 3.9, '#d8b79b');
    line([[x + 2, y - 11], [x + 4, y - 25]], '#a7bdca', 1.3);
    const tilt = hit ? walker.direction * 4 : 0;
    poly([[x - 10 + tilt, y - 25], [x - 5 + tilt, y - 31], [x + 4 + tilt, y - 33],
      [x + 13 + tilt, y - 27], [x + 14 + tilt, y - 25]], '#a9d3da');
    line([[x - 9 + tilt, y - 25], [x + 14 + tilt, y - 25]], '#e1fffc', 1);
    if (hit) {
      ctx.fillStyle = '#fff0c0';
      ctx.font = 'bold 13px system-ui';
      ctx.fillText('тук!', x + 13, y - 36);
    }
  }

  function icicle(level, state, fall, motion) {
    if (fall && fall.elapsed >= fall.impactAt) return;
    const [rx, ry] = level.anchor;
    const swing = reducedMotion.matches ? 0 : Math.sin(motion * 12) * state.wind.force * Math.min(8, state.length / 23);
    const falling = fall ? Math.pow(fall.elapsed / fall.impactAt, 2) : 0;
    const impactY = fall?.cameo ? road - 17 : road;
    const offsetX = fall ? (fall.x - state.x) * falling : 0;
    const offsetY = Math.max(0, impactY - state.y) * falling;
    const center = [...state.points, [state.x, state.y]].map(([x, y], i, points) => {
      const fraction = i / (points.length - 1);
      return [x + offsetX + swing * fraction * fraction, y + offsetY];
    });
    const radius = Math.min(17, 6.5 + state.length / 40);
    const edges = dir => center.map(([x, y], i) => {
      const prev = center[Math.max(0, i - 1)];
      const next = center[Math.min(center.length - 1, i + 1)];
      const dx = next[0] - prev[0];
      const dy = next[1] - prev[1];
      const span = Math.hypot(dx, dy) || 1;
      const t = i / (center.length - 1);
      const thickness = radius * (1 - t) ** .83 * (seed(i + dir * 5) * .23 + .82) + 1;
      return [x + dir * dy * thickness / span, y - dir * dx * thickness / span];
    });
    const left = edges(-1);
    const right = edges(1);
    const glass = ctx.createLinearGradient(rx - radius + offsetX, ry + offsetY, state.x + radius + offsetX, state.y + offsetY);
    glass.addColorStop(0, '#ecfeff');
    glass.addColorStop(.36, '#b0f6ff');
    glass.addColorStop(.65, '#4f9cbd');
    glass.addColorStop(1, '#dcfbff');
    ctx.shadowColor = '#9deaffc0';
    ctx.shadowBlur = 9;
    poly([...left, ...right.slice().reverse()], glass);
    ctx.shadowBlur = 0;
    line(left, '#e0fcffe4', 1.05);
    line(center, '#e7fcff9a', .85);
    for (let i = 3; i < center.length - 3; i += 5) {
      const [x, y] = center[i];
      const size = Math.min(4, Math.abs(right[i][0] - left[i][0]) * .34);
      line([[x - size, y - 1], [x + size, y + 1]], '#f1feff95', .88);
    }
    if (state.cracks >= 1 || state.load >= 95) {
      const x = rx + offsetX;
      const y = ry + offsetY + 5;
      line([[x - radius * .7, y], [x - 3, y + 4], [x + 2, y + 2],
        [x + 5, y + 8], [x + radius * .61, y + 7]], '#244762', 1.7);
    }
  }

  function splash(fall) {
    if (!fall || fall.elapsed < fall.impactAt || fall.elapsed > 1.32 || reducedMotion.matches) return;
    const age = fall.elapsed - fall.impactAt;
    const base = fall.cameo ? road - 17 : road;
    for (let i = 0; i < 12; i++) {
      const x = fall.x + (seed(i + 31) - .5) * 120 * age;
      const y = base + mix(-88, -26, seed(i + 9)) * age + 135 * age * age;
      const size = seed(i + 105) * 3 + 1;
      poly([[x - size, y], [x + size, y - size], [x + 1, y + size]], '#cff5ff');
    }
  }

  function draw(level, state, { pouring, aim, fall, motion }) {
    if (!ctx) return;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(canvas.clientWidth * pixelRatio);
    const h = Math.round(canvas.clientHeight * pixelRatio);
    if (!w || !h) return;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    ctx.setTransform(w / width, 0, 0, h / height, 0, 0);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    backdrop(level, state);
    street(level);
    furniture(level, state, motion);
    const time = state.time + (fall?.elapsed ?? 0);
    const walker = level.id === 'record' ? walkerAt(time, state.walkerOffset) : null;
    if (walker) renderWalker(walker, Boolean(fall?.cameo && fall.elapsed >= fall.impactAt));
    roof(level);
    if (pouring && aim !== null && level.id !== 'record') {
      ctx.setLineDash([2, 4]);
      line([[state.x, state.y], [mix(state.x, aim, .24), Math.min(state.y + 29, road)]], '#b7f2ffb5', 1.4);
      ctx.setLineDash([]);
    }
    icicle(level, state, fall, motion);
    if (pouring && state.y < road) {
      const phase = reducedMotion.matches ? .22 : motion * 4 % 1;
      circle(state.x + phase * 2, state.y + 2 + phase * 15, 2.8 - phase, '#b3f8ff');
    }
    splash(fall);
  }

  return { draw };
}
