const DropRanking = (() => {
  const byId = id => document.getElementById(id);
  const dialog = byId('leaderboard');
  const endpoint = '?view=sasulka';
  let refreshId = 0;
  let runId = 0;

  async function api(body) {
    const initData = window.Telegram?.WebApp?.initData;
    if (!initData) throw new Error('Открой игру в Telegram, чтобы попасть в рейтинг сасунов.');
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': initData },
      body: JSON.stringify(body),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result) throw new Error(result?.error || 'Рейтинг сасунов недоступен.');
    return result;
  }

  async function refresh() {
    const id = ++refreshId;
    const status = byId('rank-status');
    const table = byId('rank-rows');
    const order = byId('rank-sort').value;
    status.textContent = 'Загрузка…';
    table.replaceChildren();
    try {
      const { rows, name } = await api({ op: 'ranking', sort: order });
      if (id !== refreshId) return;
      table.replaceChildren(...rows.map((row, index) => {
        const tr = document.createElement('tr');
        for (const value of [(index + 1) + '. ' + row.name, row.record + ' см', row.solved, row.points]) {
          const td = document.createElement('td');
          td.textContent = value;
          tr.append(td);
        }
        return tr;
      }));
      status.textContent = [
        name ? 'Твой ник ' + name + '.' : 'Укажи @username в профиле Telegram и открой игру заново.',
        !rows.length && 'Пока никто не насасал на рейтинг.',
      ].filter(Boolean).join(' ');
    } catch (error) {
      if (id === refreshId) status.textContent = error.message;
    }
  }

  function clearStatus() {
    runId++;
    byId('score-status').hidden = true;
  }

  async function submit(record) {
    const id = ++runId;
    const status = byId('score-status');
    status.hidden = false;
    status.textContent = 'Проверяем результат…';
    try {
      const score = await api({ op: 'result', ...record });
      if (id === runId) status.textContent = score.gained ? '+' + score.gained + ' очков в рейтинге.' : 'Результат принят. Лучший уже в таблице.';
      if (dialog.open) await refresh();
    } catch (error) {
      if (id === runId) status.textContent = error.message;
    }
  }

  byId('ranking').addEventListener('click', () => { dialog.showModal(); refresh(); });
  byId('rank-sort').addEventListener('change', refresh);
  return { submit, dialog, clearStatus };
})();
