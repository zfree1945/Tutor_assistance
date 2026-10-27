// Inline Streamlit CCv2 component. Uses only official AMap JS API 2.0.
// Geocoding and routes intentionally stay in Python, behind explicit actions.
const instances = new WeakMap();
const SDK_TIMEOUT = 18000;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function eventId() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
}

function loadSDK(jsKey, securityCode) {
  const identity = `${jsKey}\n${securityCode}`;
  const existing = window.__tutorAMapLoader;
  if (existing && existing.identity !== identity) {
    return Promise.reject(new Error("地图凭据已修改，请刷新页面后重新加载地图。"));
  }
  if (existing) return existing.promise;

  // The browser JS key/security code are designed for the browser SDK. Never
  // pass the separately configured Web service key into this component.
  window._AMapSecurityConfig = { securityJsCode: securityCode };
  const entry = { identity, promise: null };
  entry.promise = new Promise((resolve, reject) => {
    const callback = `__tutorAMapReady_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const script = document.createElement("script");
    let settled = false;
    let timer;
    const finish = (message) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      script.onerror = script.onload = null;
      delete window[callback];
      if (message) {
        script.remove();
        if (window.__tutorAMapLoader === entry) delete window.__tutorAMapLoader;
        reject(new Error(message));
      } else {
        resolve(window.AMap);
      }
    };
    window[callback] = () => {
      if (window.AMap?.Map) finish();
      else finish("地图脚本未能初始化，请检查 JS API Key、安全密钥和网络。 ");
    };
    script.src = `https://webapi.amap.com/maps?${new URLSearchParams({
      v: "2.0", key: jsKey, plugin: "AMap.MarkerCluster", callback,
    })}`;
    script.async = true;
    script.referrerPolicy = "strict-origin-when-cross-origin";
    script.onerror = () => finish("地图加载失败，请检查网络和高德 JS API 配置。订单筛选仍可继续使用。");
    script.onload = () => { if (window.AMap?.Map) finish(); };
    timer = setTimeout(() => finish("地图加载超时，请检查网络后手动重试。"), SDK_TIMEOUT);
    document.head.appendChild(script);
  });
  window.__tutorAMapLoader = entry;
  return entry.promise;
}

function loadCluster(AMap) {
  if (AMap.MarkerCluster) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("点聚合插件加载超时，请手动重试。")), SDK_TIMEOUT);
    AMap.plugin(["AMap.MarkerCluster"], () => {
      clearTimeout(timer);
      if (AMap.MarkerCluster) resolve();
      else reject(new Error("点聚合插件不可用，请检查高德地图服务权限。"));
    });
  });
}

function possible(point) {
  return ["possible", "possible_match", "可能匹配", "无法解析", "待确认"].includes(point.status);
}

function areaScale(point) {
  return ["small", "medium", "large"].includes(point.area_scale) ? point.area_scale : "medium";
}

function approximate(point) {
  return ["approximate", "area"].includes(point.precision);
}

function originIcon() {
  const node = element("div", "tutor-map-origin");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M4 10 12 4 20 10V21H4Z");
  svg.appendChild(path);
  node.appendChild(svg);
  return node;
}

const AREA_DESCRIPTIONS = {
  small: "附近地标参考，范围较小",
  medium: "道路或社区参考位置",
  large: "范围较大，仅供参考所在片区",
};

function pin(point, selected = false) {
  const area = point.precision === "area";
  const node = element("button", `tutor-map-pin${possible(point) ? " possible" : ""}${selected ? " selected" : ""}${area ? ` area area-${areaScale(point)}` : ""}`, `${approximate(point) ? "约 · " : ""}${point.number || "•"}`);
  node.type = "button";
  node.setAttribute("aria-label", `订单 ${point.number || point.id}，${possible(point) ? "可能匹配，虚线边框" : "明确匹配，实线边框"}${approximate(point) ? "，大致定位，具体地点待确认" : ""}`);
  node.dataset.orderId = point.id;
  return node;
}

function status(state, title, detail, retry = false) {
  state.status.replaceChildren(
    element("div", "tutor-map-status-title", title),
    element("div", "tutor-map-status-detail", detail),
  );
  if (retry) {
    const button = element("button", "tutor-map-action", "重新加载地图");
    button.type = "button";
    button.onclick = () => initialize(state);
    state.status.appendChild(button);
  }
  state.status.hidden = false;
}

function hideRawPreview(state) {
  if (!state.rawPreview) return;
  cancelAnimationFrame(state.rawPreview.frame);
  state.rawPreview.view.removeAttribute("aria-describedby");
  state.rawPreview.node.remove();
  state.rawPreview = null;
}

function showRawPreview(state, point, view) {
  if (state.rawPreview?.view === view) return;
  hideRawPreview(state);
  const node = element("div", "tutor-map-raw-tooltip");
  node.id = `order-preview-${eventId()}`;
  node.setAttribute("role", "tooltip");
  node.dataset.orderId = point.id;
  node.append(
    element("div", "tutor-map-raw-title", `原始订单 · ${point.number || point.id}`),
    element("div", "tutor-map-raw-text", point.raw_text || "未提供原始订单"),
  );
  // A body-level overlay avoids clipping at the map edge, beside the marker.
  document.body.appendChild(node);
  view.setAttribute("aria-describedby", node.id);
  const preview = state.rawPreview = {node, view, frame: null};
  const position = () => {
    if (state.dead || !view.isConnected || !state.selectedPin?.isConnected) {
      hideRawPreview(state);
      return;
    }
    const anchor = state.selectedPin.getBoundingClientRect();
    const box = node.getBoundingClientRect();
    let left = anchor.right + 12;
    // Small screens can lack room on the right; keep the preview on-screen.
    if (left + box.width > innerWidth - 12) left = Math.max(12, anchor.left - box.width - 12);
    node.style.left = `${left}px`;
    node.style.top = `${Math.max(12, Math.min(anchor.top + (anchor.height - box.height) / 2, innerHeight - box.height - 12))}px`;
    preview.frame = requestAnimationFrame(position);
  };
  position();
}

function popup(state, point, emit = false) {
  if (!state.map || state.dead) return;
  const AMap = state.AMap;
  hideRawPreview(state);
  state.info?.close();
  const content = element("div", "tutor-map-popup");
  content.appendChild(element("div", "tutor-map-popup-title", `订单 ${point.number || ""}`));
  content.appendChild(element("div", "tutor-map-popup-detail", [point.grade, point.subject].filter(Boolean).join(" · ") || "科目／年级待确认"));
  content.appendChild(element("div", "tutor-map-popup-detail", point.pay || "报酬待确认"));
  if (point.precision === "area") {
    content.appendChild(element("div", "tutor-map-popup-detail", `约略位置：${point.location_label || "所在区域"}`));
    content.appendChild(element("div", "tutor-map-popup-detail", `${AREA_DESCRIPTIONS[areaScale(point)]}，具体上课地点待确认。`));
  }
  content.appendChild(element("div", "tutor-map-popup-detail", `直线距离：${point.distance_label || "距离未知"}`));
  const view = element("button", "tutor-map-popup-button", "查看订单");
  view.type = "button";
  view.onmouseenter = view.onfocus = () => showRawPreview(state, point, view);
  view.onmouseleave = () => hideRawPreview(state);
  view.onblur = () => { if (!view.matches(":hover")) hideRawPreview(state); };
  view.addEventListener("wheel", (event) => {
    const preview = state.rawPreview;
    if (preview?.view !== view || preview.node.scrollHeight <= preview.node.clientHeight) return;
    preview.node.scrollTop += event.deltaY;
    event.preventDefault();
    event.stopPropagation();
  }, {passive: false});
  view.onclick = () => {
    state.pendingSelected = point.id;
    state.emit("selected", { id: point.id, event_id: eventId() });
  };
  content.appendChild(view);
  state.popupPointId = point.id;
  state.popupContent = content;
  state.info = new AMap.InfoWindow({ content, offset: new AMap.Pixel(0, -24), closeWhenClickMap: true });
  state.info.open(state.map, [point.lng, point.lat]);
  state.localSelected = point.id;
  updateSelection(state, point);
  if (emit) {
    state.pendingSelected = point.id;
    state.emit("selected", { id: point.id, event_id: eventId() });
  }
}

function updateSelection(state, point) {
  state.selectedMarker?.setMap(null);
  state.selectedMarker = null;
  state.selectedPin = null;
  if (!point) return;
  const node = pin(point, true);
  state.selectedPin = node;
  node.onclick = (event) => { event.stopPropagation(); popup(state, point, true); };
  state.selectedMarker = new state.AMap.Marker({
    position: [point.lng, point.lat], content: node,
    offset: new state.AMap.Pixel(-17, -17), zIndex: 250,
    bubble: false, map: state.map,
  });
}

function clusterPopup(state, points, position) {
  hideRawPreview(state);
  state.popupPointId = null;
  const content = element("div", "tutor-map-popup");
  content.appendChild(element("div", "tutor-map-popup-title", `${points.length} 单位于相近位置`));
  const orders = element("div", "tutor-map-popup-orders");
  points.forEach((point) => {
    const button = element("button", "tutor-map-popup-button", `订单 ${point.number} · ${point.subject || "科目待确认"}`);
    button.type = "button";
    button.onclick = () => popup(state, point, true);
    orders.appendChild(button);
  });
  content.appendChild(orders);
  state.info?.close();
  state.info = new state.AMap.InfoWindow({ content, offset: new state.AMap.Pixel(0, -24), closeWhenClickMap: true });
  state.info.open(state.map, position);
}

function clusterPoints(state, context) {
  const records = context.data || [];
  const ids = new Set(records.map((item) => item.orderId));
  if (ids.size) return state.data.points.filter((point) => ids.has(point.id));
  // The SDK also exposes a position on its single-point render context.
  const pos = context.marker.getPosition?.();
  if (!pos) return [];
  const lng = pos.getLng?.() ?? pos.lng;
  const lat = pos.getLat?.() ?? pos.lat;
  return state.data.points.filter((point) => Math.abs(point.lng - lng) < 1e-7 && Math.abs(point.lat - lat) < 1e-7);
}

function renderPoints(state) {
  hideRawPreview(state);
  state.cluster?.setMap(null);
  state.cluster = null;
  state.originMarker?.setMap(null);
  state.originMarker = null;
  state.info?.close();
  const AMap = state.AMap;
  const points = state.data.points || [];
  if (points.length) {
    state.cluster = new AMap.MarkerCluster(
      state.map,
      points.map((point) => ({ lnglat: [point.lng, point.lat], orderId: point.id })),
      {
        gridSize: 58, maxZoom: 21, zoomOnClick: false,
        renderMarker: (context) => {
          const point = clusterPoints(state, context)[0];
          if (!point) return;
          const node = pin(point, point.id === state.localSelected);
          node.onclick = (event) => { event.stopPropagation(); popup(state, point, true); };
          context.marker.setContent(node);
          context.marker.setOffset(new AMap.Pixel(-17, -17));
        },
        renderClusterMarker: (context) => {
          const members = clusterPoints(state, context);
          const areas = members.filter(point => point.precision === "area");
          const area = areas.length > 0;
          const scale = ["large", "medium", "small"].find(value => areas.some(point => areaScale(point) === value));
          const rough = members.some(approximate);
          const node = element("button", `tutor-map-cluster${members.some(possible) ? " possible" : ""}${area ? ` area area-${scale}` : ""}`, `${rough ? "约 · " : ""}${context.count}`);
          node.type = "button";
          node.setAttribute("aria-label", `${context.count} 个订单${members.some(possible) ? "，含可能匹配订单，虚线边框" : "，明确匹配，实线边框"}${rough ? "，含大致定位" : ""}，点击展开`);
          node.onclick = (event) => {
            event.stopPropagation();
            const members = clusterPoints(state, context);
            const position = context.marker.getPosition();
            const same = members.length && members.every((p) => Math.abs(p.lng - members[0].lng) < 1e-6 && Math.abs(p.lat - members[0].lat) < 1e-6);
            if (members.length && (same || state.map.getZoom() >= 18)) {
              clusterPopup(state, members, position);
            } else {
              state.map.setZoomAndCenter(Math.min(state.map.getZoom() + 2, 20), position);
            }
          };
          context.marker.setContent(node);
          context.marker.setOffset(new AMap.Pixel(-22, -22));
        },
      },
    );
  }
  const origin = state.data.origin;
  if (origin) {
    const node = originIcon();
    node.setAttribute("role", "img");
    node.setAttribute("aria-label", `出发点：${origin.label || "出发点"}`);
    state.originMarker = new AMap.Marker({
      position: [origin.lng, origin.lat], content: node,
      offset: new AMap.Pixel(-16, -16), zIndex: 200,
      title: origin.label || "出发点", map: state.map, bubble: false,
    });
  }
  updateSelection(state, points.find((point) => point.id === state.localSelected));
}

function showAll(state) {
  if (!state.map) return;
  const points = [...(state.data.points || [])];
  if (state.data.origin) points.push(state.data.origin);
  if (!points.length) return;
  const lngs = points.map((point) => point.lng);
  const lats = points.map((point) => point.lat);
  let west = Math.min(...lngs), east = Math.max(...lngs);
  let south = Math.min(...lats), north = Math.max(...lats);
  // Keep the active origin at the centre while fitting the visible orders.
  const origin = state.data.origin;
  if (origin) {
    const dx = Math.max(origin.lng - west, east - origin.lng, 0.003);
    const dy = Math.max(origin.lat - south, north - origin.lat, 0.003);
    west = Math.max(-180, origin.lng - dx);
    east = Math.min(180, origin.lng + dx);
    south = Math.max(-85, origin.lat - dy);
    north = Math.min(85, origin.lat + dy);
  }
  const bounds = new state.AMap.Bounds(
    [west, south], [east, north],
  );
  state.map.setBounds(bounds, false, [70, 45, 55, 45]);
  if (origin) state.map.setCenter([origin.lng, origin.lat]);
  // An origin-only or coincident set should not zoom to individual buildings.
  if (state.map.getZoom() > 16) state.map.setZoom(16);
}

function reconcile(state) {
  if (!state.map || state.dead) return;
  state.hint.hidden = !state.data.picking;
  state.map.setDefaultCursor(state.data.picking ? "crosshair" : "grab");
  const signature = JSON.stringify([state.data.points, state.data.origin]);
  const changed = state.pointSignature !== signature;
  if (changed) {
    state.pointSignature = signature;
    renderPoints(state);
    // Fit newly resolved or filtered locations, while preserving manual pan
    // and zoom for metadata changes, sorting, and ordinary widget reruns.
    const positions = state.data.points.map(point => [point.lng, point.lat])
      .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const positionSignature = JSON.stringify([
      positions, state.data.origin ? [state.data.origin.lng, state.data.origin.lat] : null,
    ]);
    if (state.positionSignature !== positionSignature && (positions.length || state.data.origin)) {
      showAll(state);
    }
    state.positionSignature = positionSignature;
  }
  const selected = state.data.selected_id;
  const focus = JSON.stringify([selected, state.data.focus_token]);
  if (focus !== state.focusSignature) {
    const localEcho = selected === state.pendingSelected;
    state.pendingSelected = null;
    state.focusSignature = focus;
    state.localSelected = selected;
    const point = state.data.points.find((item) => item.id === selected);
    updateSelection(state, point);
    if (point) {
      const popupOpen = state.popupPointId === point.id && state.popupContent?.isConnected;
      // An echo of a map click must not pan the hovered button away.
      if (!localEcho || !popupOpen) state.map.setZoomAndCenter(Math.max(state.map.getZoom(), 16), [point.lng, point.lat]);
      // Selection echoes and the view button can rerun Python while hovered.
      // Keep the existing popup/button alive so that preview stays visible.
      if (!popupOpen) popup(state, point);
    }
  }
}

async function initialize(state) {
  if (state.loading || state.dead) return;
  if (!state.data.js_key || !state.data.security_code) {
    status(state, "订单地图", "请先在地图设置中填写高德 Web JS API Key 与安全密钥。原有导入与筛选可以继续使用。");
    return;
  }
  state.loading = true;
  const credentials = JSON.stringify([state.data.js_key, state.data.security_code]);
  status(state, "正在加载高德地图", "地图只展示已定位的筛选结果。未定位订单仍保留在列表中。");
  try {
    const AMap = await loadSDK(state.data.js_key, state.data.security_code);
    await loadCluster(AMap);
    if (state.dead) return;
    if (credentials !== JSON.stringify([state.data.js_key, state.data.security_code])) {
      throw new Error("地图凭据已修改，请刷新页面后重新加载地图。");
    }
    state.AMap = AMap;
    const first = state.data.origin || state.data.points?.[0];
    state.map = new AMap.Map(state.canvas, {
      viewMode: "2D", zoom: first ? 12 : 4, zooms: [3, 20],
      center: first ? [first.lng, first.lat] : [105.0, 35.0],
      resizeEnable: true, showIndoorMap: false, mapStyle: "amap://styles/normal",
    });
    state.map.on("click", (event) => {
      if (!state.data.picking || !event.lnglat) return;
      const lng = event.lnglat.getLng?.() ?? event.lnglat.lng;
      const lat = event.lnglat.getLat?.() ?? event.lnglat.lat;
      if (Number.isFinite(lng) && Number.isFinite(lat)) {
        state.emit("picked", { lng, lat, event_id: eventId() });
      }
    });
    state.map.on("error", () => status(state, "地图暂不可用", "请检查高德凭据、访问域名和账号额度。修改凭据后刷新页面；订单仍可在列表中查看。"));
    state.status.hidden = true;
    state.pointSignature = null;
    state.positionSignature = null;
    state.focusSignature = null;
    reconcile(state);
    state.observer = new ResizeObserver(() => state.map?.resize());
    state.observer.observe(state.canvas);
  } catch (error) {
    state.observer?.disconnect();
    state.info?.close();
    state.cluster?.setMap(null);
    state.map?.destroy();
    state.map = null;
    if (!state.dead) status(state, "地图暂不可用", error.message || "请检查地图配置和网络。", true);
  } finally {
    state.loading = false;
  }
}

function createState(parentElement) {
  const shell = element("div", "tutor-map-shell");
  const canvas = element("div", "tutor-map-canvas");
  canvas.setAttribute("aria-label", "筛选订单地图");
  const overlay = element("div", "tutor-map-status");
  overlay.setAttribute("role", "status");
  const actions = element("div", "tutor-map-actions");
  const all = element("button", "tutor-map-action", "查看全部位置");
  const home = element("button", "tutor-map-action", "回到出发点");
  all.type = home.type = "button";
  actions.append(all, home);
  const hint = element("div", "tutor-map-pick-hint", "点击地图选择位置，确认后才会保存");
  hint.hidden = true;
  const legend = element("div", "tutor-map-legend");
  [["origin", "出发点"], ["", "明确匹配（实线）"], ["possible", "可能匹配（虚线）"], ["approximate", "大致定位"]].forEach(([kind, title]) => {
    const item = element("span", "tutor-map-legend-item");
    const icon = kind === "origin" ? originIcon() : element("span", `tutor-map-legend-dot ${kind}`, kind === "approximate" ? "约" : "1");
    icon.setAttribute("aria-hidden", "true");
    item.append(icon, document.createTextNode(title));
    legend.appendChild(item);
  });
  shell.append(canvas, actions, hint, legend, overlay);
  const slot = parentElement.querySelector(".tutor-map-slot") || parentElement;
  slot.appendChild(shell);
  const state = { shell, canvas, status: overlay, hint, data: {}, dead: false, map: null, loading: false };
  all.onclick = () => showAll(state);
  home.onclick = () => {
    const origin = state.data.origin;
    if (origin && state.map) state.map.setZoomAndCenter(14, [origin.lng, origin.lat]);
  };
  state.cleanup = () => {
    state.dead = true;
    hideRawPreview(state);
    state.observer?.disconnect();
    state.info?.close();
    state.cluster?.setMap(null);
    state.map?.destroy();
    state.map = null;
    shell.remove();
    instances.delete(parentElement);
  };
  return state;
}

export default function(component) {
  const { parentElement, data, setTriggerValue } = component;
  let state = instances.get(parentElement);
  if (!state || state.dead) {
    state = createState(parentElement);
    instances.set(parentElement, state);
  }
  const oldCredentials = JSON.stringify([state.data.js_key, state.data.security_code]);
  state.data = { points: [], ...data };
  state.emit = setTriggerValue;
  state.shell.style.height = `${state.data.height || 560}px`;
  const newCredentials = JSON.stringify([state.data.js_key, state.data.security_code]);
  if (state.map && oldCredentials !== newCredentials) {
    state.observer?.disconnect();
    state.cluster?.setMap(null);
    state.map.destroy();
    state.map = null;
    status(state, "地图凭据已修改", "请刷新页面后使用新的地图凭据。");
  } else if (state.map) {
    reconcile(state);
  } else if (!state.loading) {
    initialize(state);
  }
  // Streamlit invokes cleanup when this component unmounts, not on prop changes.
  return state.cleanup;
}
