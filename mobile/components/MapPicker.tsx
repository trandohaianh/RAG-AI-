import React, { forwardRef, useImperativeHandle, useMemo, useRef } from "react";
import { Platform, StyleSheet, View } from "react-native";
import { WebView } from "react-native-webview";
import { buildMapHtml, parseMapMessage } from "../lib/mapHtml";
import { colors } from "../lib/theme";

export type MapPickerHandle = { setPin: (lat: number, lng: number, zoom?: number) => void };

type Props = {
  /** Toạ độ ban đầu của bản đồ; có ghim sẵn nếu `pinned`. */
  initial: { lat: number; lng: number };
  zoom: number;
  pinned: boolean;
  onPin: (lat: number, lng: number) => void;
  onError?: (message: string) => void;
};

/**
 * Bản đồ chọn vị trí (M13, Q6): Leaflet + OpenStreetMap trong WebView. Trên bản web của Expo
 * (không có WebView) dùng iframe cùng trang HTML.
 */
const MapPicker = forwardRef<MapPickerHandle, Props>(function MapPicker({ initial, zoom, pinned, onPin, onError }, ref) {
  // Trang HTML chỉ dựng một lần: đổi ghim về sau đi qua postMessage, không tải lại bản đồ.
  const html = useMemo(() => buildMapHtml(initial, zoom, pinned), []); // eslint-disable-line react-hooks/exhaustive-deps
  const webRef = useRef<WebView>(null);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const handlers = useRef({ onPin, onError });
  handlers.current = { onPin, onError };

  useImperativeHandle(ref, () => ({
    setPin(lat, lng, z) {
      const message = JSON.stringify({ type: "setPin", lat, lng, zoom: z });
      if (Platform.OS === "web") frameRef.current?.contentWindow?.postMessage(message, "*");
      else webRef.current?.postMessage(message);
    },
  }));

  function receive(raw: unknown) {
    const msg = parseMapMessage(raw);
    if (msg?.type === "pin") handlers.current.onPin(msg.lat, msg.lng);
    else if (msg?.type === "error") handlers.current.onError?.(msg.message);
  }

  if (Platform.OS === "web") {
    return (
      <View style={styles.box}>
        <WebFrame html={html} frameRef={frameRef} onMessage={receive} />
      </View>
    );
  }
  return (
    <View style={styles.box}>
      <WebView
        ref={webRef}
        originWhitelist={["https://*", "about:*"]}
        source={{ html }}
        onMessage={(event) => receive(event.nativeEvent.data)}
        // Không cho bản đồ điều hướng đi nơi khác (ví dụ bấm link ghi công).
        onShouldStartLoadWithRequest={(request) => request.url === "about:blank" || request.url.startsWith("data:")}
        setSupportMultipleWindows={false}
        javaScriptEnabled
        style={styles.map}
      />
    </View>
  );
});

function WebFrame({
  html,
  frameRef,
  onMessage,
}: {
  html: string;
  frameRef: React.MutableRefObject<HTMLIFrameElement | null>;
  onMessage: (raw: unknown) => void;
}) {
  const listener = useRef(onMessage);
  listener.current = onMessage;
  React.useEffect(() => {
    const handle = (event: MessageEvent) => {
      if (event.source === frameRef.current?.contentWindow) listener.current(event.data);
    };
    window.addEventListener("message", handle);
    return () => window.removeEventListener("message", handle);
  }, [frameRef]);
  return React.createElement("iframe", {
    ref: frameRef,
    srcDoc: html,
    title: "Bản đồ chọn vị trí",
    sandbox: "allow-scripts",
    style: { border: 0, width: "100%", height: "100%" },
  });
}

const styles = StyleSheet.create({
  box: { height: 300, borderRadius: 12, overflow: "hidden", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  map: { flex: 1 },
});

export default MapPicker;
