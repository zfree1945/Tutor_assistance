/* Deliberately small offline SDK double. Geography is not a production map. */
(() => {
  const audit = window.__amapAudit = {maps: [], clusters: [], markers: [], info: []};
  const coordinates = value => Array.isArray(value) ? value : [value?.lng ?? 120.70, value?.lat ?? 28.00];
  class Events {
    constructor() { this.handlers = {}; }
    on(name, callback) { (this.handlers[name] ||= []).push(callback); return this; }
    off(name, callback) { this.handlers[name] = (this.handlers[name] || []).filter(x => x !== callback); }
    emit(name, value) { for (const handler of this.handlers[name] || []) handler(value); }
  }
  class LngLat {
    constructor(lng, lat) { this.lng = Number(lng); this.lat = Number(lat); }
    getLng() { return this.lng; }
    getLat() { return this.lat; }
  }
  class Map extends Events {
    constructor(container, options = {}) {
      super(); this.container = typeof container === 'string' ? document.getElementById(container) : container;
      this.options = options; this.center = options.center || [120.70, 28.00]; this.zoom = options.zoom || 12;
      this.container.dataset.mockMap = 'true'; this.container.style.position = 'relative';
      this.container.style.background = '#e9eff4'; this.container.style.overflow = 'hidden';
      const notice = document.createElement('div'); notice.textContent = '离线测试地图 · 模拟高德 SDK';
      notice.style.cssText = 'position:absolute;bottom:12px;left:12px;color:#536575;font-size:12px;z-index:2';
      this.container.append(notice);
      this.container.addEventListener('click', event => {
        if (event.target === this.container) this.emit('click', {lnglat: new LngLat(120.701, 28.001)});
      });
      audit.maps.push(this); setTimeout(() => this.emit('complete'), 0);
    }
    add(items) { for (const item of Array.isArray(items) ? items : [items]) item?.setMap?.(this); }
    remove(items) { for (const item of Array.isArray(items) ? items : [items]) item?.setMap?.(null); }
    setZoomAndCenter(zoom, center) { this.zoom = zoom; this.center = coordinates(center); }
    getZoom() { return this.zoom; }
    getCenter() { return new LngLat(...coordinates(this.center)); }
    setFitView() { this.fitted = true; }
    setBounds(bounds) { this.bounds = bounds; this.fitted = true; }
    setZoom(value) { this.zoom = value; }
    setCenter(value) { this.center = coordinates(value); }
    setDefaultCursor(value) { this.container.style.cursor = value; }
    resize() {}
    destroy() { this.destroyed = true; this.container.replaceChildren(); }
  }
  class Marker extends Events {
    constructor(options = {}) {
      super(); this.options = options; this.position = options.position || [120.7, 28];
      this.element = document.createElement('div'); this.element.dataset.mockMarker = 'true';
      this.element.style.cssText = 'position:absolute;cursor:pointer;z-index:5';
      this.element.style.left = `${28 + audit.markers.length * 17 % 57}%`;
      this.element.style.top = `${28 + audit.markers.length * 13 % 45}%`;
      this.element.addEventListener('click', event => {event.stopPropagation(); this.emit('click', {target:this});});
      this.setContent(options.content || '●'); audit.markers.push(this);
      if (options.map) this.setMap(options.map);
    }
    setMap(map) { this.element.remove(); this.map = map; if(map) map.container.append(this.element); }
    setContent(content) {
      this.element.replaceChildren();
      if(typeof content === 'string') this.element.innerHTML = content; else if(content) this.element.append(content);
    }
    setOffset(value) { this.offset = value; }
    getPosition() { return new LngLat(...coordinates(this.position)); }
    setPosition(value) { this.position = value; }
    setExtData(value) { this.extData = value; }
    getExtData() { return this.extData; }
  }
  class InfoWindow {
    constructor(options = {}) {this.options=options; this.content=options.content; audit.info.push(this);}
    setContent(value) {this.content=value;}
    open(map, position) {
      this.close(); this.map=map; this.position=position;
      this.element=document.createElement('div'); this.element.dataset.mockInfo='true';
      this.element.style.cssText='position:absolute;left:15px;top:15px;z-index:9;background:white;border-radius:10px;padding:12px;box-shadow:0 2px 8px #16324f22';
      if(typeof this.content==='string') this.element.innerHTML=this.content; else if(this.content) this.element.append(this.content);
      map.container.append(this.element);
    }
    close() {this.element?.remove();}
  }
  class MarkerCluster extends Events {
    constructor(map, data, options = {}) {
      super(); this.map=map; this.data=data; this.options=options; this.markers=[];
      audit.clusters.push(this);
      for(const point of data) {
        const marker=new Marker({position:point.lnglat || [point.lng,point.lat]});
        this.markers.push(marker);
        const renderer=options.renderMarker || options.renderClusterMarker;
        renderer?.({marker,data:[point],count:1}); marker.setMap(map);
      }
    }
    setMap(map) {this.map=map; for(const marker of this.markers) marker.setMap(map);}
    setData(data) {this.data=data;}
  }
  class Pixel {constructor(x,y){this.x=x;this.y=y;}}
  class Bounds {constructor(southwest,northeast){this.southwest=southwest;this.northeast=northeast;}}
  window.AMap={Map,Marker,MarkerCluster,InfoWindow,LngLat,Pixel,Bounds,Size:Pixel,plugin:(_,callback)=>callback()};
  const callback = new URL(document.currentScript.src).searchParams.get('callback');
  if(callback && typeof window[callback]==='function') window[callback]();
})();
