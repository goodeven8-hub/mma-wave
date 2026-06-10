// ============================================================
// DATA — イベント（向こう3ヶ月分）
// ============================================================
// イベントデータ（data/events.jsonからfetchして上書き）
let EVENTS = [];

const WATCH_LABELS = {
  'ufc-fp':    { label:'UFC Fight Pass', cls:'ufc-fp' },
  'amazon':    { label:'Amazon Prime',   cls:'amazon' },
  'rizin-live':{ label:'RIZIN LIVE',     cls:'rizin-live' },
  'abema':     { label:'ABEMA',          cls:'abema' },
  'tbs':       { label:'TBS',            cls:'tbs' },
  'one-fc':    { label:'ONE FC',         cls:'one-fc' },
  'wowow':     { label:'WOWOW',          cls:'wowow' },
};

const ORG_BADGE = {
  ufc:   'ufc-badge',
  rizin: 'rizin-badge',
  one:   'one-badge',
};

// アフィリエイトバナー（カテゴリ別）
const AFFILIATE_BANNERS = {
  ufc: `
    <div class="aff-label">UFC を観るなら</div>
    <div class="aff-row">
      <a class="aff-btn ufc-fp" href="https://www.ufcfightpass.com/?utm_source=mmawave" target="_blank" rel="noopener">UFC Fight Pass で観る</a>
      <a class="aff-btn amazon" href="https://www.amazon.co.jp/primevideo?tag=mmawave-22" target="_blank" rel="noopener">Amazon Prime Video で観る</a>
    </div>`,
  rizin: `
    <div class="aff-label">RIZIN を観るなら</div>
    <div class="aff-row">
      <a class="aff-btn rizin-live" href="https://live.rizinff.com/?utm_source=mmawave" target="_blank" rel="noopener">RIZIN LIVE で観る</a>
      <a class="aff-btn abema" href="https://abema.tv/search?q=RIZIN&utm_source=mmawave" target="_blank" rel="noopener">ABEMA で観る</a>
    </div>`,
  one: `
    <div class="aff-label">ONE Championship を観るなら</div>
    <div class="aff-row">
      <a class="aff-btn one-fc" href="https://www.onefc.com/plus/?utm_source=mmawave" target="_blank" rel="noopener">ONE FC+ で観る</a>
    </div>`,
};

// ============================================================
// 日本の祝日 2026
// ============================================================
const JP_HOLIDAYS = new Set([
  '2026-01-01','2026-01-12','2026-02-11','2026-02-23',
  '2026-03-20','2026-04-29','2026-05-03','2026-05-04',
  '2026-05-05','2026-07-20','2026-08-11','2026-09-21',
  '2026-09-23','2026-10-12','2026-11-03','2026-11-23',
]);

// ============================================================
// STATE
// ============================================================
let currentCat     = 'all';
let eventsExpanded = false;
let calYear, calMonth, activeCalDate = null;
let allNews        = [];   // fetch済みデータ
let displayedCount = 0;
const PAGE_SIZE    = 8;

// ============================================================
// DARK MODE
// ============================================================
const darkToggle = document.getElementById('darkToggle');
if (localStorage.getItem('theme') === 'dark') {
  document.body.classList.add('dark');
  darkToggle.textContent = '☀️';
}
darkToggle.addEventListener('click', () => {
  document.body.classList.toggle('dark');
  const isDark = document.body.classList.contains('dark');
  darkToggle.textContent = isDark ? '☀️' : '🌙';
  localStorage.setItem('theme', isDark ? 'dark' : 'light');
});

// ============================================================
// EVENTS レンダリング
// ============================================================
function daysFromNow(dateStr) {
  const today = new Date(); today.setHours(0,0,0,0);
  return Math.round((new Date(dateStr) - today) / 86400000);
}

function renderEvents() {
  const row   = document.getElementById('eventsRow');
  const btn   = document.getElementById('eventsMore');
  const today = new Date(); today.setHours(0,0,0,0);
  const limit = new Date(today); limit.setMonth(limit.getMonth() + 3);

  let filtered = EVENTS.filter(e => {
    const d = new Date(e.date);
    return d >= today && d <= limit && (currentCat === 'all' || e.cat === currentCat);
  });

  const INITIAL = 4;
  const shown   = eventsExpanded ? filtered : filtered.slice(0, INITIAL);

  const dayNames = ['日','月','火','水','木','金','土'];
  row.innerHTML = shown.map((e, i) => {
    const days    = daysFromNow(e.date);
    const dateObj = new Date(e.date);
    const dateStr = `${dateObj.getFullYear()}年${dateObj.getMonth()+1}月${dateObj.getDate()}日`;
    const dow     = dayNames[dateObj.getDay()];
    const badgeCls = days <= 7 ? '' : 'soon';
    const dayLabel = days === 0 ? '今日' : days === 1 ? '明日' : `${days}日後`;
    const watches  = e.watch.map(w => {
      const wl = WATCH_LABELS[w] || { label:w, cls:'' };
      return `<span class="watch-chip ${wl.cls}">${wl.label}</span>`;
    }).join('');
    const featured = i === 0 && !eventsExpanded ? 'featured-card' : '';
    return `
      <div class="event-card ${featured}">
        <div class="event-card-header">
          <span class="org-badge ${ORG_BADGE[e.cat]}">${e.cat.toUpperCase()}</span>
          <span class="event-days-badge ${badgeCls}">${dayLabel}</span>
        </div>
        <div class="event-name">${e.name}</div>
        <div class="event-matchup">${e.matchup}</div>
        <div class="event-date-row">📆 ${dateStr}(${dow})　🕐 ${e.time || '?'} JST　📍 ${e.venue}</div>
        <div class="event-watch-row">${watches}</div>
      </div>`;
  }).join('');

  if (filtered.length <= INITIAL) {
    btn.style.display = 'none';
  } else {
    btn.style.display = 'block';
    btn.textContent = eventsExpanded
      ? '折りたたむ ▲'
      : `もっと見る（残り ${filtered.length - INITIAL} 件）▼`;
  }
}

document.getElementById('eventsMore').addEventListener('click', () => {
  eventsExpanded = !eventsExpanded;
  renderEvents();
});

// ============================================================
// NEWS 動的レンダリング
// ============================================================
const ORG_LABEL = { ufc:'UFC', rizin:'RIZIN', one:'ONE' };

function buildNewsCard(article) {
  const badgeCls = ORG_BADGE[article.cat] || '';
  const label    = ORG_LABEL[article.cat] || article.cat.toUpperCase();
  return `
    <article class="news-card" data-cat="${article.cat}" data-id="${article.id}">
      <div class="card-cat ${badgeCls}">${label}</div>
      <div class="card-body">
        <span class="card-date">${article.date}</span>
        <h3 class="card-title">${article.title}</h3>
        <p class="card-excerpt">${article.excerpt.slice(0, 60)}…</p>
        <span class="read-more">続きを読む →</span>
      </div>
    </article>`;
}

function getFilteredNews() {
  return allNews.filter(a => currentCat === 'all' || a.cat === currentCat);
}

function renderNews(reset = false) {
  const grid    = document.getElementById('newsGrid');
  const loadBtn = document.getElementById('loadMore');
  const filtered = getFilteredNews();

  if (reset) {
    grid.innerHTML = '';
    displayedCount = 0;
  }

  const slice = filtered.slice(displayedCount, displayedCount + PAGE_SIZE);
  slice.forEach(a => {
    grid.insertAdjacentHTML('beforeend', buildNewsCard(a));
  });
  displayedCount += slice.length;

  // クリックリスナー付与
  grid.querySelectorAll('.news-card:not([data-bound])').forEach(card => {
    card.dataset.bound = '1';
    card.addEventListener('click', () => {
      const id = card.dataset.id;
      const article = allNews.find(a => a.id === id);
      if (article) openArticleModal(article);
    });
  });

  // もっと読む
  if (displayedCount < filtered.length) {
    loadBtn.style.display = 'block';
    loadBtn.textContent = `もっと読む（残り ${filtered.length - displayedCount} 件）`;
    loadBtn.disabled = false;
  } else {
    loadBtn.style.display = filtered.length > 0 ? 'block' : 'none';
    loadBtn.textContent = 'すべて表示しました';
    loadBtn.disabled = true;
  }
}

document.getElementById('loadMore').addEventListener('click', function() {
  renderNews(false);
});

// ============================================================
// ARTICLE MODAL
// ============================================================
function openArticleModal(article) {
  const modal   = document.getElementById('articleModal');
  const badge   = document.getElementById('articleModalBadge');
  const dateEl  = document.getElementById('articleModalDate');
  const titleEl = document.getElementById('articleModalTitle');
  const excerptEl = document.getElementById('articleModalExcerpt');
  const srcEl   = document.getElementById('articleModalSource');
  const affEl   = document.getElementById('articleModalAffiliate');

  badge.textContent = ORG_LABEL[article.cat] || article.cat.toUpperCase();
  badge.className   = `article-modal-badge ${ORG_BADGE[article.cat] || ''}`;
  dateEl.textContent  = article.date;
  titleEl.textContent = article.title;
  excerptEl.textContent = article.excerpt;
  srcEl.href = article.source_url;
  srcEl.textContent = `元記事を読む（${article.source_name}）→`;
  affEl.innerHTML = AFFILIATE_BANNERS[article.cat] || '';

  modal.classList.add('open');
}

document.getElementById('articleModalClose').addEventListener('click', () => {
  document.getElementById('articleModal').classList.remove('open');
});
document.getElementById('articleModal').addEventListener('click', e => {
  if (e.target === document.getElementById('articleModal'))
    document.getElementById('articleModal').classList.remove('open');
});

// ============================================================
// CATEGORY FILTER
// ============================================================
const catBtns = document.querySelectorAll('.cat-btn');

catBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    catBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentCat     = btn.dataset.cat;
    eventsExpanded = false;
    renderEvents();
    renderNews(true);
    activeCalDate = null;
    renderCalendar();
  });
});

// ============================================================
// CALENDAR
// ============================================================
function getArticleDates() {
  const dates = new Set();
  getFilteredNews().forEach(a => {
    const d = a.date.replace(/\./g, '-');
    dates.add(d);
  });
  return dates;
}

function renderCalendar() {
  const monthEl      = document.getElementById('calMonth');
  const body         = document.getElementById('calBody');
  const articleDates = getArticleDates();

  monthEl.textContent = `${calYear}年 ${calMonth + 1}月`;

  const firstDay    = new Date(calYear, calMonth, 1).getDay();
  const startOffset = firstDay === 0 ? 6 : firstDay - 1;
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const todayStr    = new Date().toISOString().slice(0, 10);

  let html = '';
  let day  = 1;
  for (let row = 0; row < 6; row++) {
    if (day > daysInMonth) break;
    html += '<tr>';
    for (let col = 0; col < 7; col++) {
      const idx = row * 7 + col;
      if (idx < startOffset || day > daysInMonth) {
        html += '<td></td>';
      } else {
        const dateStr  = `${calYear}-${String(calMonth+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
        const isSat    = col === 5;
        const isSun    = col === 6;
        const isHoliday= JP_HOLIDAYS.has(dateStr);
        const hasArt   = articleDates.has(dateStr);
        const isActive = activeCalDate === dateStr;
        const isToday  = dateStr === todayStr;

        let cls = [];
        if (isSat)            cls.push('cal-sat');
        if (isSun || isHoliday) cls.push('cal-sun');
        if (isToday)          cls.push('cal-today');
        if (hasArt)           cls.push('cal-has-article');
        if (isActive)         cls.push('cal-active');

        html += `<td class="${cls.join(' ')}" data-date="${dateStr}">${day}</td>`;
        day++;
      }
    }
    html += '</tr>';
  }
  body.innerHTML = html;

  body.querySelectorAll('td.cal-has-article').forEach(td => {
    td.addEventListener('click', () => {
      if (activeCalDate === td.dataset.date) {
        activeCalDate = null;
      } else {
        activeCalDate = td.dataset.date;
      }
      renderNews(true);

      // カレンダーフィルターを上書き
      if (activeCalDate) {
        const grid = document.getElementById('newsGrid');
        const full = activeCalDate.replace(/-/g, '.');
        grid.querySelectorAll('.news-card').forEach(c => {
          const d     = c.querySelector('.card-date')?.textContent;
          c.classList.toggle('hidden', d !== full);
        });
      }
      renderCalendar();
    });
  });
}

document.getElementById('calPrev').addEventListener('click', () => {
  const limit = new Date(); limit.setFullYear(limit.getFullYear() - 1);
  const prev  = new Date(calYear, calMonth - 1, 1);
  if (prev >= new Date(limit.getFullYear(), limit.getMonth(), 1)) {
    calMonth = prev.getMonth(); calYear = prev.getFullYear();
    activeCalDate = null; renderCalendar();
  }
});
document.getElementById('calNext').addEventListener('click', () => {
  const limit = new Date(); limit.setMonth(limit.getMonth() + 3);
  const next  = new Date(calYear, calMonth + 1, 1);
  if (next <= new Date(limit.getFullYear(), limit.getMonth(), 1)) {
    calMonth = next.getMonth(); calYear = next.getFullYear();
    activeCalDate = null; renderCalendar();
  }
});

// ============================================================
// CHAMPION MODAL
// ============================================================
const champWidget   = document.getElementById('champWidget');
const champModal    = document.getElementById('champModal');
const champClose    = document.getElementById('champModalClose');
const modalTabs     = document.querySelectorAll('.modal-tab');
const modalContents = document.querySelectorAll('.modal-content[data-org]');

champWidget.addEventListener('click', () => champModal.classList.add('open'));
champClose.addEventListener('click',  () => champModal.classList.remove('open'));
champModal.addEventListener('click', e => { if (e.target === champModal) champModal.classList.remove('open'); });
document.addEventListener('keydown',  e => { if (e.key === 'Escape') {
  champModal.classList.remove('open');
  document.getElementById('articleModal').classList.remove('open');
}});

modalTabs.forEach(tab => {
  tab.addEventListener('click', () => {
    modalTabs.forEach(t => t.classList.remove('active'));
    modalContents.forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.querySelector(`.modal-content[data-org="${tab.dataset.org}"]`).classList.add('active');
  });
});

// UFC サブタブ
document.querySelectorAll('.sub-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const parent = tab.closest('.modal-content');
    parent.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
    parent.querySelectorAll('.sub-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(tab.dataset.sub).classList.add('active');
  });
});

function renderChampions(data) {
  if (!data) return;

  // UFC 男子
  const menTbody = document.querySelector('#champUfcMen tbody');
  if (menTbody && data.ufc?.men?.length) {
    menTbody.innerHTML = data.ufc.men.map(c =>
      `<tr><td class="weight">${c.weight}</td><td class="name">${c.name}</td></tr>`
    ).join('');
  }

  // UFC 女子
  const womenTbody = document.querySelector('#champUfcWomen tbody');
  if (womenTbody && data.ufc?.women?.length) {
    womenTbody.innerHTML = data.ufc.women.map(c =>
      `<tr><td class="weight">${c.weight}</td><td class="name">${c.name}</td></tr>`
    ).join('');
  }

  // UFC P4P
  const p4pEl = document.getElementById('champUfcP4p');
  const menP4p   = data.ufc?.p4p_men   || [];
  const womenP4p = data.ufc?.p4p_women || [];
  if (p4pEl && (menP4p.length || womenP4p.length)) {
    const col = (list, title) => `
      <div class="p4p-col">
        <div class="p4p-title">${title}</div>
        ${list.map(r => `<div class="p4p-row ${r.rank===1?'p4p-champ':''}">
          <span class="p4p-rank">${r.rank}</span>${r.name}</div>`).join('')}
      </div>`;
    p4pEl.innerHTML = `<div class="p4p-cols">${col(menP4p,'男子 P4P')}${col(womenP4p,'女子 P4P')}</div>`;
  }

  // サイドバー更新
  const sidebarTable = document.getElementById('champSidebarTable');
  if (sidebarTable) {
    const rows = [];
    const shortWeight = w => w.replace('級','').replace('女子','女').slice(0,5);
    (data.ufc?.men || []).slice(0, 3).forEach(c =>
      rows.push(`<tr><td class="champ-org">UFC ${shortWeight(c.weight)}</td><td>${c.name}</td></tr>`)
    );
    (data.rizin || []).slice(0, 2).forEach(c =>
      rows.push(`<tr><td class="champ-org">RIZIN ${shortWeight(c.weight)}</td><td>${c.name}</td></tr>`)
    );
    if (rows.length) sidebarTable.innerHTML = rows.join('');
  }
}


// ============================================================
// INIT
// ============================================================
const now = new Date();
calYear   = now.getFullYear();
calMonth  = now.getMonth();

Promise.all([
  fetch('data/events.json').then(r => r.json()).catch(() => []),
  fetch('data/news.json').then(r => r.json()).catch(() => []),
  fetch('data/champions.json').then(r => r.json()).catch(() => null),
]).then(([eventsData, newsData, champsData]) => {
  EVENTS  = eventsData;
  allNews = newsData;
  renderEvents();
  renderNews(true);
  renderCalendar();
  renderChampions(champsData);
});
