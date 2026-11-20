"use client";
import { useEffect, useRef } from "react";
import "leaflet/dist/leaflet.css";

export type MapRoute = {
  color: string;
  label: string;
  base?: [number, number] | null;
  stops: { lat: number; lng: number; label: string }[];
};

export const ROUTE_COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4b5563"];

/** Bản đồ tuyến (Leaflet + OpenStreetMap). Dùng circleMarker để không phụ thuộc ảnh icon. */
export default function RouteMap({
  routes,
  unplaced = [],
}: {
  routes: MapRoute[];
  unplaced?: { lat: number; lng: number; label: string }[];
}) {
  const holder = useRef<HTMLDivElement>(null);
  const mapRef = useRef<import("leaflet").Map | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !holder.current) return;
      if (!mapRef.current) {
        mapRef.current = L.map(holder.current, { zoomControl: true }).setView([21.0285, 105.8542], 12);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: "&copy; OpenStreetMap contributors",
        }).addTo(mapRef.current);
      }
      const map = mapRef.current;
      map.eachLayer((layer) => {
        if (!(layer instanceof L.TileLayer)) map.removeLayer(layer);
      });
      const bounds: [number, number][] = [];
      routes.forEach((route) => {
        const path: [number, number][] = [];
        if (route.base) {
          path.push(route.base);
          L.circleMarker(route.base, { radius: 8, color: route.color, fillColor: "#fff", fillOpacity: 1, weight: 3 })
            .bindTooltip(`${route.label} — điểm xuất phát`)
            .addTo(map);
        }
        route.stops.forEach((stop, index) => {
          path.push([stop.lat, stop.lng]);
          L.circleMarker([stop.lat, stop.lng], {
            radius: 9,
            color: route.color,
            fillColor: route.color,
            fillOpacity: 0.9,
            weight: 2,
          })
            .bindTooltip(`${index + 1}. ${stop.label}`, { permanent: false })
            .addTo(map);
        });
        if (path.length > 1)
          L.polyline(path, { color: route.color, weight: 3, opacity: 0.75, dashArray: "6 6" }).addTo(map);
        bounds.push(...path);
      });
      unplaced.forEach((p) => {
        L.circleMarker([p.lat, p.lng], { radius: 7, color: "#6b7280", fillColor: "#e5e7eb", fillOpacity: 1, weight: 2 })
          .bindTooltip(`Chưa xếp: ${p.label}`)
          .addTo(map);
        bounds.push([p.lat, p.lng]);
      });
      if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
    })();
    return () => {
      cancelled = true;
    };
  }, [routes, unplaced]);

  useEffect(
    () => () => {
      mapRef.current?.remove();
      mapRef.current = null;
    },
    [],
  );

  return <div ref={holder} className="h-[420px] w-full overflow-hidden rounded-xl border border-slate-200" />;
}
