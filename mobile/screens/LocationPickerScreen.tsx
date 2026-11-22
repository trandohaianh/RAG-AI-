import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as Location from "expo-location";
import * as api from "../lib/api";
import {
  applyPendingFieldAction,
  cacheFieldCase,
  enqueueFieldAction,
  isSecureOfflineStorageAvailable,
  prepareFieldAction,
} from "../lib/fieldOffline";
import { colors, radius } from "../lib/theme";
import GradientButton from "../components/GradientButton";
import MapPicker, { type MapPickerHandle } from "../components/MapPicker";

type Pin = { lat: number; lng: number };
type Source = api.LocationSource;

const DEFAULT_CENTER = { lat: 21.0285, lng: 105.8542 };

/**
 * M13 — đặt vị trí khách (docs/design/M13_dia_chi_toa_do.md, Q1/Q5/Q6).
 * Trưởng đội: lưu thẳng toạ độ. Kỹ thuật viên được giao: gửi vị trí đề xuất (xếp hàng khi mất sóng).
 */
export default function LocationPickerScreen({ route, navigation }: any) {
  const item = route.params.item as api.FieldCase;
  const suggestion = item.location_suggestion ?? null;
  const start: Pin | null = suggestion
    ? { lat: suggestion.latitude, lng: suggestion.longitude }
    : item.latitude != null && item.longitude != null
      ? { lat: item.latitude, lng: item.longitude }
      : null;
  const [pin, setPin] = useState<Pin | null>(start);
  const [source, setSource] = useState<Source>(suggestion ? "suggestion" : "manual");
  const [address, setAddress] = useState(item.address ?? "");
  const [results, setResults] = useState<api.FieldGeocodeResult[]>([]);
  const [chosen, setChosen] = useState<number | null>(null);
  const [accuracy, setAccuracy] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<"search" | "gps" | "save" | null>(null);
  const [isOwner, setIsOwner] = useState<boolean | null>(null);
  const map = useRef<MapPickerHandle>(null);

  useEffect(() => {
    let alive = true;
    api
      .listFieldWorkspaces()
      .then((list) => alive && setIsOwner(list.find((w) => w.id === item.workspace_id)?.role === "owner"))
      .catch(() => alive && setIsOwner(false)); // mất mạng: gửi đề xuất (an toàn hơn), xếp hàng được
    return () => {
      alive = false;
    };
  }, [item.workspace_id]);

  function place(next: Pin, from: Source, zoom?: number) {
    setPin(next);
    setSource(from);
    map.current?.setPin(next.lat, next.lng, zoom);
  }

  async function search() {
    const text = address.trim();
    if (text.length < 5) return setMessage("Nhập địa chỉ đầy đủ hơn để tra.");
    setBusy("search");
    setMessage("");
    try {
      const data = await api.geocodeAddress(item.workspace_id, text);
      setResults(data.results);
      setAccuracy(null);
      if (data.results.length) {
        setChosen(0);
        place({ lat: data.results[0].latitude, lng: data.results[0].longitude }, "geocoded", 17);
      } else {
        setChosen(null);
        setMessage(data.message ?? "Không tìm thấy — hãy chạm lên bản đồ để đặt ghim.");
      }
    } catch (error) {
      setMessage(api.isNetworkError(error) ? "Cần có mạng để tìm toạ độ. Bạn vẫn có thể dùng GPS." : api.getErrorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function useGps() {
    setBusy("gps");
    setMessage("");
    try {
      // Chỉ xin quyền lúc bấm nút; không theo dõi vị trí nền.
      const permission = await Location.requestForegroundPermissionsAsync();
      if (!permission.granted) {
        setMessage("Chưa cho phép dùng vị trí. Bật quyền vị trí cho FieldOps trong cài đặt máy.");
        return;
      }
      const here = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      setAccuracy(here.coords.accuracy ?? null);
      setChosen(null);
      place({ lat: Number(here.coords.latitude.toFixed(6)), lng: Number(here.coords.longitude.toFixed(6)) }, "gps", 18);
    } catch (error) {
      setMessage(`Không lấy được vị trí của máy: ${api.getErrorMessage(error)}`);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!pin) return setMessage("Chưa có vị trí — tìm theo địa chỉ, dùng GPS, hoặc chạm lên bản đồ.");
    setBusy("save");
    setMessage("");
    try {
      if (isOwner) {
        await api.saveCaseLocation(item.id, pin.lat, pin.lng, source);
        navigation.goBack();
        return;
      }
      const suggestSource = source === "gps" || source === "geocoded" ? source : "manual";
      const action = prepareFieldAction({
        kind: "location_suggest",
        case_id: item.id,
        latitude: pin.lat,
        longitude: pin.lng,
        source: suggestSource,
      });
      try {
        await api.suggestCaseLocation(item.id, {
          latitude: pin.lat,
          longitude: pin.lng,
          source: suggestSource,
          client_action_id: action.id,
        });
        Alert.alert("Đã gửi vị trí", "Trưởng đội sẽ xác nhận trước khi dùng để xếp tuyến.");
        navigation.goBack();
      } catch (error) {
        if (!api.isNetworkError(error)) throw error;
        // Mất sóng ở nhà khách: giữ đề xuất an toàn trong máy, gửi khi có mạng (như M12).
        const user = await api.loadUser();
        if (!user || !(await isSecureOfflineStorageAvailable())) {
          throw new Error("Mất mạng và máy này không lưu offline an toàn được — thử lại khi có sóng.");
        }
        const queued = await enqueueFieldAction(user.id, action);
        await cacheFieldCase(user.id, item.workspace_id, applyPendingFieldAction(item, queued, user.id));
        Alert.alert("Đã lưu trong máy", "Mất mạng: vị trí sẽ tự gửi khi có sóng.");
        navigation.goBack();
      }
    } catch (error) {
      setMessage(api.getErrorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  const precision =
    source === "geocoded" && chosen != null ? results[chosen] : source === "suggestion" ? suggestion : null;

  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.title}>Vị trí khách — ca #{item.id}</Text>
      {suggestion && source === "suggestion" ? (
        <Text style={styles.info}>
          Đang hiện vị trí đề xuất ({suggestion.source === "gps" ? "GPS tại nhà khách" : suggestion.source === "geocoded" ? "tự tra từ địa chỉ" : "chọn trên bản đồ"}).
        </Text>
      ) : null}

      <TouchableOpacity style={styles.gpsButton} onPress={useGps} disabled={busy !== null}>
        {busy === "gps" ? <ActivityIndicator color="#fff" /> : <Ionicons name="locate" size={18} color="#fff" />}
        <Text style={styles.gpsText}>Tôi đang ở nhà khách (dùng GPS)</Text>
      </TouchableOpacity>
      {source === "gps" && accuracy != null ? (
        <Text style={accuracy <= 30 ? styles.good : styles.warn}>
          Sai số GPS khoảng ±{Math.round(accuracy)} m{accuracy > 30 ? " — ra chỗ thoáng rồi thử lại, hoặc kéo ghim cho đúng." : ""}
        </Text>
      ) : null}

      <Text style={styles.label}>Hoặc tìm theo địa chỉ</Text>
      <View style={styles.searchRow}>
        <TextInput
          value={address}
          onChangeText={setAddress}
          maxLength={300}
          placeholder="Số nhà, đường, phường, tỉnh/thành"
          placeholderTextColor={colors.textMuted}
          style={styles.input}
          returnKeyType="search"
          onSubmitEditing={search}
        />
        <TouchableOpacity style={styles.searchButton} onPress={search} disabled={busy !== null} accessibilityLabel="Tìm toạ độ">
          {busy === "search" ? <ActivityIndicator color="#fff" /> : <Ionicons name="search" size={18} color="#fff" />}
        </TouchableOpacity>
      </View>
      {results.length > 1
        ? results.map((r, i) => (
            <TouchableOpacity
              key={`${r.latitude},${r.longitude}`}
              style={[styles.result, chosen === i && styles.resultActive]}
              onPress={() => {
                setChosen(i);
                place({ lat: r.latitude, lng: r.longitude }, "geocoded", 17);
              }}
            >
              <Text style={styles.resultText} numberOfLines={2}>{r.label}</Text>
              <Text style={styles.resultMeta}>{r.precision_label}</Text>
            </TouchableOpacity>
          ))
        : null}
      {precision?.precision_label ? (
        <Text style={precision.precision === "house" ? styles.good : styles.warn}>
          {precision.precision === "house" ? precision.precision_label : `⚠ ${precision.precision_label} — kéo ghim tới đúng nhà khách.`}
        </Text>
      ) : null}

      <View style={{ marginTop: 12 }}>
        <MapPicker
          ref={map}
          initial={start ?? DEFAULT_CENTER}
          zoom={start ? 17 : 12}
          pinned={start != null}
          onPin={(lat, lng) => {
            // Chỉnh lại ghim của GPS / địa chỉ / đề xuất thì giữ nguồn; đặt ghim mới thì là "tay".
            if (!pin) setSource("manual");
            setPin({ lat, lng });
          }}
          onError={setMessage}
        />
      </View>
      <Text style={styles.hint}>Chạm lên bản đồ để đặt ghim, kéo ghim để chỉnh. Bản đồ © OpenStreetMap contributors.</Text>
      {pin ? <Text style={styles.coords}>Toạ độ: {pin.lat.toFixed(6)}, {pin.lng.toFixed(6)}</Text> : null}
      {message ? <Text style={styles.error} accessibilityRole="alert">{message}</Text> : null}

      <GradientButton
        title={isOwner ? "Lưu vị trí này" : "Gửi vị trí cho trưởng đội"}
        onPress={save}
        loading={busy === "save"}
        disabled={!pin || isOwner === null}
        style={{ marginTop: 14 }}
      />
      {isOwner === false ? (
        <Text style={styles.hint}>Vị trí bạn gửi là đề xuất: trưởng đội xác nhận rồi mới dùng để xếp tuyến.</Text>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, paddingBottom: 40, backgroundColor: colors.background },
  title: { color: colors.text, fontSize: 18, fontWeight: "800" },
  info: { marginTop: 8, backgroundColor: colors.primarySoft, color: colors.text, borderRadius: 10, padding: 10, fontSize: 12 },
  gpsButton: { marginTop: 14, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, backgroundColor: colors.primary, borderRadius: radius.md, paddingVertical: 13 },
  gpsText: { color: "#fff", fontWeight: "800", fontSize: 14 },
  label: { color: colors.text, fontSize: 12, fontWeight: "700", marginTop: 16, marginBottom: 6 },
  searchRow: { flexDirection: "row", gap: 8 },
  input: { flex: 1, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10, color: colors.text, fontSize: 13, backgroundColor: colors.card },
  searchButton: { width: 46, alignItems: "center", justifyContent: "center", backgroundColor: colors.text, borderRadius: radius.md },
  result: { marginTop: 6, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 9, backgroundColor: colors.card },
  resultActive: { borderColor: colors.primary },
  resultText: { color: colors.text, fontSize: 12 },
  resultMeta: { color: colors.textMuted, fontSize: 11, marginTop: 2 },
  good: { marginTop: 8, color: colors.success, fontSize: 12, fontWeight: "600" },
  warn: { marginTop: 8, backgroundColor: colors.warningSoft, color: colors.text, borderRadius: 10, padding: 10, fontSize: 12 },
  hint: { marginTop: 6, color: colors.textMuted, fontSize: 11, lineHeight: 16 },
  coords: { marginTop: 4, color: colors.text, fontSize: 12 },
  error: { marginTop: 10, color: colors.destructive, fontSize: 12 },
});
