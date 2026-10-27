const instances = new WeakMap();

export default function ({parentElement, data, setStateValue}) {
  const host = parentElement.querySelector('.origin-search');
  let s = instances.get(host);
  if (!s) {
    host.innerHTML = `<label for="origin-query">搜索出发点</label>
      <input id="origin-query" role="combobox" aria-autocomplete="list" aria-controls="origin-options"
        aria-expanded="false" autocomplete="off" placeholder="输入学校、小区、地铁站">
      <div id="origin-options" role="listbox" aria-label="出发点位置建议" hidden></div>
      <div class="origin-status" role="status"></div>`;
    s = {input: host.querySelector('input'), list: host.querySelector('[role=listbox]'),
      status: host.querySelector('[role=status]'), timer: null, composing: false,
      active: -1, lastSent: data.query || '', initialized: false, open: false};
    instances.set(host, s);
  }
  s.emit = setStateValue;
  s.data = data;
  const hide = () => { s.list.hidden = true; s.input.setAttribute('aria-expanded', 'false'); };
  const choose = (index) => {
    clearTimeout(s.timer);
    s.input.value = s.data.options[index];
    s.open = false;
    hide();
    s.status.textContent = '已选择：' + s.data.options[index];
    s.emit('selection', {query: s.data.query, index});
  };
  const show = () => {
    s.list.replaceChildren();
    s.active = -1;
    if (!s.open || s.input.value.trim() !== s.data.query || !s.data.options.length) { hide(); return; }
    s.data.options.forEach((text, index) => {
      const option = document.createElement('div');
      option.id = 'origin-option-' + index;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      option.textContent = text;
      option.onmousedown = (event) => event.preventDefault();
      option.onclick = () => choose(index);
      s.list.appendChild(option);
    });
    s.list.hidden = false;
    s.input.setAttribute('aria-expanded', 'true');
  };
  const changed = () => {
    clearTimeout(s.timer);
    s.open = true;
    hide();
    s.status.textContent = s.input.value.trim() ? '正在查找地点…' : '';
    // Immediately invalidate the old location, even during debounce/IME input.
    s.emit('selection', null);
    if (s.composing) return;
    s.timer = setTimeout(() => {
      if (!host.isConnected) return;
      const query = s.input.value.trim();
      if (query !== s.lastSent) {
        s.lastSent = query;
        s.emit('query', query);
      } else {
        s.status.textContent = s.data.message || '';
        show();
      }
    }, 450);
  };
  if (!s.initialized) {
    s.input.value = data.selected || data.query || '';
    s.initialized = true;
  }
  s.input.disabled = data.disabled;
  s.input.oninput = changed;
  s.input.oncompositionstart = () => { s.composing = true; clearTimeout(s.timer); };
  s.input.oncompositionend = () => { s.composing = false; changed(); };
  s.input.onfocus = () => { s.open = true; show(); };
  s.input.onblur = () => { s.open = false; hide(); };
  s.input.onkeydown = (event) => {
    if (s.composing || event.isComposing) return;
    if (event.key === 'Escape') { s.open = false; hide(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (s.list.hidden) { s.open = true; show(); }
      const options = Array.from(s.list.children);
      if (!options.length) return;
      s.active = (s.active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      options.forEach((option, i) => option.setAttribute('aria-selected', String(i === s.active)));
      s.input.setAttribute('aria-activedescendant', options[s.active].id);
      options[s.active].scrollIntoView({block: 'nearest'});
    }
    if (event.key === 'Enter' && !s.list.hidden && s.active >= 0) {
      event.preventDefault(); choose(s.active);
    }
  };
  if (s.input.value.trim() === data.query) {
    s.status.textContent = data.message || (data.options.length ? '请选择确切的出发点位置' : '');
    show();
  }
  // The DOM instance survives rerenders; keep a pending debounce across them.
  // A detached instance must not dispatch after navigating away.

}
