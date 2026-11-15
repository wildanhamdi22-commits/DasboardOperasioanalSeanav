/* Basemap bersama untuk semua peta Seanav: Citra satelit (default), Citra + label, dan Peta jalan. */
window.SeanavBasemap = function (map, opts) {
  opts = opts || {};
  const esri = "https://server.arcgisonline.com/ArcGIS/rest/services/";
  const esriAttr = "Citra &copy; Esri, Maxar, Earthstar Geographics";
  const ref = {referrerPolicy: "strict-origin-when-cross-origin"};
  const imagery = () => L.tileLayer(esri + "World_Imagery/MapServer/tile/{z}/{y}/{x}", Object.assign({maxZoom: 19, maxNativeZoom: 18, attribution: esriAttr}, ref));
  const labels = () => L.tileLayer(esri + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", Object.assign({maxZoom: 19, maxNativeZoom: 18, pane: "overlayPane"}, ref));
  const roads = () => L.tileLayer(esri + "Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}", Object.assign({maxZoom: 19, maxNativeZoom: 18, pane: "overlayPane", opacity: 0.8}, ref));
  const layers = {
    "Citra + label": L.layerGroup([imagery(), roads(), labels()]),
    "Citra satelit": imagery(),
    "Peta jalan": L.tileLayer(esri + "World_Street_Map/MapServer/tile/{z}/{y}/{x}", Object.assign({maxZoom: 19, attribution: "&copy; Esri, HERE, Garmin, OpenStreetMap contributors"}, ref)),
    "Topografi": L.tileLayer(esri + "World_Topo_Map/MapServer/tile/{z}/{y}/{x}", Object.assign({maxZoom: 19, attribution: "&copy; Esri, HERE, Garmin, OpenStreetMap contributors"}, ref))
  };
  let chosen = opts.defaultLayer || "Citra + label";
  try { const saved = localStorage.getItem("seanav-basemap"); if (saved && layers[saved]) chosen = saved; } catch (e) {}
  layers[chosen].addTo(map);
  L.control.layers(layers, null, {position: "topright", collapsed: true}).addTo(map);
  map.on("baselayerchange", (e) => { try { localStorage.setItem("seanav-basemap", e.name); } catch (err) {} });
  L.control.scale({imperial: false}).addTo(map);
  return layers;
};
