/* Shared UI interactions used across screens — theme toggle, avatar menu,
   cosmetic cart (client-side only, no backend call per product decision:
   checkout/payment is explicitly out of scope), signal-panel drawer/ticker. */

function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  const next = isDark ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('pathwise_theme', next);
  const icon = document.getElementById('themeicon');
  const label = document.getElementById('themelabel');
  if (icon) icon.textContent = isDark ? '☀' : '\u{1F319}';
  if (label) label.textContent = isDark ? 'Light mode' : 'Dark mode';
}

(function initTheme() {
  const saved = localStorage.getItem('pathwise_theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
})();

function toggleAvatarMenu(id) {
  document.querySelectorAll('.avatarmenu').forEach(m => { if (m.id !== id) m.classList.remove('open'); });
  document.getElementById(id).classList.toggle('open');
}
document.addEventListener('click', (e) => {
  if (!e.target.closest('.avatarwrap')) {
    document.querySelectorAll('.avatarmenu').forEach(m => m.classList.remove('open'));
  }
});

/* Cosmetic cart — a real, browsable list of added items, persisted to
   localStorage. Still no backend call: checkout/payment is explicitly out
   of scope (PRD Section 2) — /cart only ever lists what's here and lets you
   remove things, it never places an order. Persisted (not in-memory)
   because this is a multi-page server-rendered app, not the original
   wireframe's single-page JS demo — an in-memory counter/list would
   silently reset on every full page navigation. */
/* Namespaced per user (window.PATHWISE_USER_ID, set by every page) so
   switching users in the same browser doesn't show the previous user's
   cart — plain 'pathwise_cart_items' was one shared bucket for everyone. */
function cartStorageKey() {
  return `pathwise_cart_items:${window.PATHWISE_USER_ID || 'anon'}`;
}

function getCartItems() {
  try {
    const raw = JSON.parse(localStorage.getItem(cartStorageKey()) || '[]');
    return Array.isArray(raw) ? raw : [];
  } catch (e) {
    return [];
  }
}

function setCartItems(items) {
  localStorage.setItem(cartStorageKey(), JSON.stringify(items));
  renderCartBadge();
}

function renderCartBadge() {
  const n = getCartItems().length;
  document.querySelectorAll('.cartcount').forEach(el => el.textContent = n);
}
/* Deferred to DOMContentLoaded, not called inline here — window.PATHWISE_USER_ID
   is set by an inline <script> that runs AFTER this file (script tags execute
   in document order), so calling this immediately would read the 'anon'
   bucket instead of the real signed-in user's cart. */
document.addEventListener('DOMContentLoaded', renderCartBadge);

function addToCartCosmetic(item) {
  const items = getCartItems();
  items.push(item || {});
  setCartItems(items);
  const t = document.getElementById('toast');
  if (t) {
    t.style.display = 'block';
    clearTimeout(window._toastTimer);
    window._toastTimer = setTimeout(() => t.style.display = 'none', 1800);
  }
}

/* /cart page rendering — no-op on every other page since #cart-items
   won't exist there. */
function renderCartPage() {
  const container = document.getElementById('cart-items');
  if (!container) return;

  const items = getCartItems();
  container.innerHTML = '';
  renderCartCompanions(items);

  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'emptystate';
    empty.textContent = 'Your cart is empty. Browse the catalog and add a course or path to get started.';
    container.appendChild(empty);
    updateCartTotal(0);
    return;
  }

  let total = 0;
  items.forEach((item, idx) => {
    const price = Number(item.price) || 0;
    total += price;

    const row = document.createElement('div');
    row.className = 'cartrow';

    const link = document.createElement('a');
    link.href = item.href || '#';
    link.textContent = item.title || 'Untitled item';

    const right = document.createElement('div');
    right.className = 'cartrow-right';

    const priceEl = document.createElement('span');
    priceEl.textContent = `$${price.toFixed(0)}`;

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'cartremove';
    removeBtn.textContent = 'Remove';
    removeBtn.addEventListener('click', () => {
      const current = getCartItems();
      current.splice(idx, 1);
      setCartItems(current);
      renderCartPage();
    });

    right.append(priceEl, removeBtn);
    row.append(link, right);
    container.appendChild(row);
  });
  updateCartTotal(total);
}

function updateCartTotal(total) {
  const totalEl = document.getElementById('cart-total');
  if (totalEl) totalEl.textContent = `$${total.toFixed(0)}`;
}
document.addEventListener('DOMContentLoaded', renderCartPage);

/* "Complete your learning" rail — catalog items that share tags with what's
   already in the cart, plus the "why" line explaining the pairing. Cart
   contents only exist in localStorage (no server-side cart), so the
   ids/kinds are sent to the backend on every render rather than being read
   off a DB row.

   Cart entries added before this feature shipped only have {title, price,
   href} (no id/kind) — fall back to parsing the id out of href (always
   "/course/{id}" or "/path/{id}", set at add-to-cart time) rather than
   requiring a cart clear for old items to get companions. */
function cartItemToPair(item) {
  if (item.id && item.kind) return `${item.kind}:${item.id}`;
  const m = (item.href || '').match(/^\/(course|path)\/(.+)$/);
  return m ? `${m[1]}:${m[2]}` : null;
}

function renderCartCompanions(items) {
  const wrap = document.getElementById('cart-companions-wrap');
  const grid = document.getElementById('cart-companions');
  if (!wrap || !grid) return;

  const pairs = items.map(cartItemToPair).filter(Boolean);

  if (!pairs.length) {
    wrap.style.display = 'none';
    grid.innerHTML = '';
    return;
  }

  fetch(`/api/v1/recommendations/cart-companions?items=${encodeURIComponent(pairs.join(','))}`)
    .then(r => r.ok ? r.json() : { html: '' })
    .then(data => {
      if (!data.html) {
        grid.innerHTML = '';
        wrap.style.display = 'none';
        return;
      }
      // Server-rendered via the same agent_recommended() macro as the real
      // Agent Recommended block (headline, badge, narrative, highlights,
      // tile grid) — not a bespoke card built here, so it stays visually
      // identical if that component ever changes.
      grid.innerHTML = data.html;
      wrap.style.display = '';
    })
    .catch(() => { wrap.style.display = 'none'; });
}

document.addEventListener('DOMContentLoaded', () => {
  // Nav links and topic-strip tabs are plain <a href> navigations, not SPA
  // routes — track with sendBeacon (immediate=true) so the event is fired
  // off before the browser follows the link away from this page.
  document.querySelectorAll('.topcats a, .navlinks a').forEach(a => {
    a.addEventListener('click', () => {
      if (window.pathwiseTrack) {
        window.pathwiseTrack({
          event_type: 'click',
          target: a.textContent.trim(),
          client_ts: new Date().toISOString(),
        }, true);
      }
    });
  });

});

/* Delegated (not querySelectorAll().forEach at load time) because cta-btns
   can also arrive later — e.g. the cart page's "Complete your learning"
   rail is injected via fetch() well after DOMContentLoaded, and a one-time
   forEach would never see those buttons. */
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.cta-btn');
  if (!btn) return;
  // Some cta-btns (recommendation tiles) sit inside an <a> that links to
  // the product page — the cosmetic add-to-cart action shouldn't also
  // navigate away, so stop it reaching the anchor.
  e.preventDefault();
  e.stopPropagation();
  addToCartCosmetic({
    title: btn.dataset.itemTitle,
    price: parseFloat(btn.dataset.itemPrice) || 0,
    href: btn.dataset.itemHref || '#',
    id: btn.dataset.itemId || null,
    kind: btn.dataset.itemKind || null,
  });
  showAddedToCart(btn.dataset.itemTitle);
  if (window.pathwiseTrack) {
    const itemId = btn.dataset.itemId;
    const itemKind = btn.dataset.itemKind;
    window.pathwiseTrack({
      event_type: 'add_to_cart',
      product_id: itemKind === 'course' ? itemId : null,
      path_id: itemKind === 'path' ? itemId : null,
      target: btn.dataset.itemTitle,
      client_ts: new Date().toISOString(),
    }, true);
  }
});

function expandTicker(id, btn) {
  document.getElementById(id).classList.remove('collapsed');
  btn.style.display = 'none';
}

function toggleDrawer(id, btn) {
  const d = document.getElementById(id);
  d.classList.toggle('open');
  btn.querySelector('span').textContent = d.classList.contains('open')
    ? 'Hide reasoning ▴'
    : 'Why this recommendation? ▾';
}

/* Signal panel — the server-rendered ticker only reflects events that were
   already in the DB before this page loaded, so the page's own "view" event
   (sent client-side, right now) never shows up until a later page load.
   Prepend it immediately so the panel feels live, matching the wireframe's
   "Viewing now" state. */
function showViewingNow(containerId, title) {
  prependTickerItem(containerId + '-ticker', 'Viewing now · ', title);
}

/* Same "server-rendered ticker is already stale by the time this JS runs"
   gap as showViewingNow above, for the add-to-cart action — there's exactly
   one .signalpanel (and one .ticker) on any given page, so find it directly
   rather than requiring every cta-btn call site to know its container id. */
function showAddedToCart(title) {
  const ticker = document.querySelector('.ticker');
  if (!ticker) return;
  prependTickerItem(ticker.id, 'Added to cart · ', title);
}

function prependTickerItem(tickerId, prefix, title) {
  const ticker = document.getElementById(tickerId);
  if (!ticker || !title) return;
  ticker.querySelector('.emptystate')?.remove();
  const item = document.createElement('div');
  item.className = 'tickitem new';
  item.append(prefix);
  const b = document.createElement('b');
  b.textContent = title;
  item.append(b);
  ticker.prepend(item);
}

/* Onboarding — topic chip multi-select (max 5)
   Listens on the checkbox's `change` event (fires once per real toggle),
   not `click` on the wrapping label — a label click also synthesizes a
   click on its nested input, which bubbles back through the label and
   double-fires a click listener there. */
function initTopicChips(max) {
  let selected = document.querySelectorAll('.chip.sel').length;
  const counter = document.getElementById('topiccount');
  document.querySelectorAll('.chip').forEach(chip => {
    const input = chip.querySelector('input');
    if (!input) return;
    input.addEventListener('change', () => {
      if (input.checked && selected >= max) {
        input.checked = false;
        return;
      }
      chip.classList.toggle('sel', input.checked);
      selected += input.checked ? 1 : -1;
      if (counter) counter.textContent = `${selected} / ${max} selected`;
    });
  });
}

/* Nav search — typeahead dropdown hitting /api/v1/search, debounced so every
   keystroke doesn't fire a request. Enter (or "See all results") navigates
   to /browse?q=... which reuses the same catalog grid as topic browsing. */
(function initNavSearch() {
  const input = document.getElementById('navsearch');
  const dropdown = document.getElementById('navsearchdropdown');
  if (!input || !dropdown) return;

  let debounceTimer = null;
  let activeIndex = -1;
  let currentQuery = '';

  function closeDropdown() {
    dropdown.classList.remove('open');
    dropdown.innerHTML = '';
    activeIndex = -1;
  }

  function goToResultsPage() {
    const q = input.value.trim();
    if (!q) return;
    window.location.href = `/browse?q=${encodeURIComponent(q)}`;
  }

  function renderResults(data) {
    const courses = data.courses || [];
    const paths = data.paths || [];
    dropdown.innerHTML = '';
    activeIndex = -1;

    if (!courses.length && !paths.length) {
      const empty = document.createElement('div');
      empty.className = 'searchempty';
      empty.textContent = `No matches for "${currentQuery}"`;
      dropdown.appendChild(empty);
      dropdown.classList.add('open');
      return;
    }

    paths.forEach(p => dropdown.appendChild(buildResultLink(p)));
    courses.forEach(c => dropdown.appendChild(buildResultLink(c)));

    const seeAll = document.createElement('a');
    seeAll.className = 'searchseeall';
    seeAll.href = `/browse?q=${encodeURIComponent(currentQuery)}`;
    seeAll.textContent = `See all results for "${currentQuery}"`;
    dropdown.appendChild(seeAll);

    dropdown.classList.add('open');
  }

  function buildResultLink(item) {
    const a = document.createElement('a');
    a.className = 'searchresult';
    a.href = `/${item.kind}/${item.id}`;
    const title = document.createElement('span');
    title.textContent = item.title;
    const kind = document.createElement('span');
    kind.className = 'kind';
    kind.textContent = item.kind;
    a.append(title, kind);
    return a;
  }

  input.addEventListener('input', () => {
    const q = input.value.trim();
    clearTimeout(debounceTimer);
    if (!q) {
      closeDropdown();
      return;
    }
    debounceTimer = setTimeout(() => {
      currentQuery = q;
      fetch(`/api/v1/search?q=${encodeURIComponent(q)}`)
        .then(r => r.ok ? r.json() : { courses: [], paths: [] })
        .then(data => { if (input.value.trim() === q) renderResults(data); })
        .catch(() => closeDropdown());
    }, 200);
  });

  input.addEventListener('keydown', (e) => {
    const items = dropdown.querySelectorAll('.searchresult, .searchseeall');
    if (e.key === 'ArrowDown' && items.length) {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, items.length - 1);
      items.forEach((el, i) => el.classList.toggle('active', i === activeIndex));
      items[activeIndex].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp' && items.length) {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      items.forEach((el, i) => el.classList.toggle('active', i === activeIndex));
      items[activeIndex].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeIndex >= 0 && items[activeIndex]) {
        window.location.href = items[activeIndex].getAttribute('href');
      } else {
        goToResultsPage();
      }
    } else if (e.key === 'Escape') {
      closeDropdown();
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.searchwrap')) closeDropdown();
  });
})();

/* Onboarding — goal single-select card */
function initGoalCards() {
  document.querySelectorAll('.goalcard').forEach(card => {
    card.addEventListener('click', () => {
      document.querySelectorAll('.goalcard').forEach(c => c.classList.remove('sel'));
      card.classList.add('sel');
      const input = card.querySelector('input');
      if (input) input.checked = true;
    });
  });
}
