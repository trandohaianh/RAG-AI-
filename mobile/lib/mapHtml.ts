/**
 * M13 — trang bản đồ nhỏ (Leaflet + OpenStreetMap) chạy trong WebView (Q6: 0 đồng, không khoá API).
 * Trang và app nói chuyện qua postMessage:
 *  - trang → app: {"type":"pin","lat":..,"lng":..} khi bấm bản đồ hoặc kéo ghim xong;
 *  - app → trang: {"type":"setPin","lat":..,"lng":..,"zoom"?:..} để đặt ghim theo gợi ý / GPS.
 */
export const LEAFLET_VERSION = "1.9.4";

export function buildMapHtml(center: { lat: number; lng: number }, zoom: number, pin: boolean): string {
  const init = JSON.stringify({ lat: center.lat, lng: center.lng, zoom, pin });
  return `<!doctype html>
<html><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@${LEAFLET_VERSION}/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@${LEAFLET_VERSION}/dist/leaflet.js"></script>
<style>html,body,#map{margin:0;height:100%;width:100%;background:#eef2f7}
.pin{width:26px;height:26px;border-radius:50% 50% 50% 0;background:#dc2626;transform:rotate(-45deg);
border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.45)}</style>
</head><body><div id="map"></div>
<script>
(function () {
  var init = ${init};
  function send(msg) {
    var text = JSON.stringify(msg);
    if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(text);
    else if (window.parent !== window) window.parent.postMessage(text, "*");
  }
  if (!window.L) { send({ type: "error", message: "Không tải được bản đồ (cần mạng)" }); return; }
  var map = L.map("map", { zoomControl: true }).setView([init.lat, init.lng], init.zoom);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);
  var icon = L.divIcon({ className: "", html: '<div class="pin"></div>', iconSize: [26, 26], iconAnchor: [13, 26] });
  var marker = null;
  function place(lat, lng, notify) {
    if (!marker) {
      marker = L.marker([lat, lng], { draggable: true, icon: icon }).addTo(map);
      marker.on("dragend", function () { var p = marker.getLatLng(); send({ type: "pin", lat: p.lat, lng: p.lng }); });
    } else {
      marker.setLatLng([lat, lng]);
    }
    if (notify) send({ type: "pin", lat: lat, lng: lng });
  }
  if (init.pin) place(init.lat, init.lng, false);
  map.on("click", function (e) { place(e.latlng.lat, e.latlng.lng, true); });
  function onMessage(event) {
    var msg;
    try { msg = typeof event.data === "string" ? JSON.parse(event.data) : event.data; } catch (e) { return; }
    if (!msg || msg.type !== "setPin") return;
    place(msg.lat, msg.lng, false);
    map.setView([msg.lat, msg.lng], msg.zoom || Math.max(map.getZoom(), 16));
  }
  window.addEventListener("message", onMessage);
  document.addEventListener("message", onMessage);
  send({ type: "ready" });
})();
</script></body></html>`;
}

/** Đọc tin nhắn từ trang bản đồ; bỏ qua mọi thứ không đúng dạng. */
export function parseMapMessage(raw: unknown): { type: "pin"; lat: number; lng: number } | { type: "ready" } | { type: "error"; message: string } | null {
  let msg: any;
  try {
    msg = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch {
    return null;
  }
  if (!msg || typeof msg !== "object") return null;
  if (msg.type === "pin") {
    const lat = Number(msg.lat);
    const lng = Number(msg.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
    return { type: "pin", lat: Number(lat.toFixed(6)), lng: Number(lng.toFixed(6)) };
  }
  if (msg.type === "ready") return { type: "ready" };
  if (msg.type === "error") return { type: "error", message: String(msg.message ?? "") };
  return null;
}
