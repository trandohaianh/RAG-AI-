import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Image, Modal, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as ImagePicker from "expo-image-picker";
import { Ionicons } from "@expo/vector-icons";
import * as api from "../lib/api";
import { flushPhotos, newPhotoActionId, pendingPhotos, queuePhoto, type QueuedPhoto } from "../lib/fieldPhotos";
import { colors, radius } from "../lib/theme";

/** Ảnh hiện trường trong màn hình chi tiết ca: chụp/chọn, gửi ngay, mất sóng thì xếp hàng gửi sau. */
export default function CasePhotosSection({ caseId, userId, disabled }: { caseId: number; userId: number | null; disabled?: boolean }) {
  const [photos, setPhotos] = useState<api.FieldPhoto[]>([]);
  const [queued, setQueued] = useState<QueuedPhoto[]>([]);
  const [caption, setCaption] = useState("");
  const [busy, setBusy] = useState(false);
  const [viewing, setViewing] = useState<api.FieldPhoto | null>(null);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    if (userId != null) {
      const result = await flushPhotos(userId).catch(() => null);
      if (result?.dropped.length) setMessage(`Máy chủ từ chối: ${result.dropped.join("; ")}`);
      setQueued(await pendingPhotos(userId, caseId));
    }
    try {
      setPhotos(await api.listFieldPhotos(caseId));
    } catch {
      /* offline: keep what we have */
    }
  }, [caseId, userId]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function pick(source: "camera" | "library") {
    const permission = source === "camera"
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert("Cần quyền truy cập", source === "camera" ? "Cho phép dùng camera để chụp ảnh hiện trường." : "Cho phép truy cập thư viện ảnh.");
      return;
    }
    const options: ImagePicker.ImagePickerOptions = { mediaTypes: ["images"], quality: 0.7, exif: false };
    const result = source === "camera" ? await ImagePicker.launchCameraAsync(options) : await ImagePicker.launchImageLibraryAsync(options);
    if (result.canceled || !result.assets?.length) return;
    const uri = result.assets[0].uri;
    const actionId = newPhotoActionId();
    setBusy(true);
    setMessage("");
    try {
      await api.uploadFieldPhoto(caseId, uri, caption.trim(), actionId);
      setCaption("");
      await refresh();
    } catch (err) {
      const status = api.getApiErrorStatus(err);
      if (status === undefined && userId != null) {          // lỗi mạng: giữ lại gửi sau
        await queuePhoto(userId, caseId, uri, caption.trim(), actionId);
        setCaption("");
        setQueued(await pendingPhotos(userId, caseId));
        setMessage("Mất kết nối — ảnh đã lưu trên máy và sẽ tự gửi khi có mạng.");
      } else {
        setMessage(api.getErrorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function remove(photo: api.FieldPhoto) {
    try {
      await api.deleteFieldPhoto(photo.id);
      setViewing(null);
      await refresh();
    } catch (err) {
      Alert.alert("Không xoá được", api.getErrorMessage(err));
    }
  }

  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Text style={styles.title}>Ảnh hiện trường</Text>
        <Text style={styles.meta}>{photos.length} ảnh{queued.length ? ` · ${queued.length} chờ gửi` : ""}</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 8 }}>
        {photos.map((photo) => (
          <TouchableOpacity key={photo.id} onPress={() => setViewing(photo)}>
            <Image source={{ uri: photo.thumb_url }} style={styles.thumb} />
          </TouchableOpacity>
        ))}
        {queued.map((photo) => (
          <View key={photo.id}>
            <Image source={{ uri: photo.path }} style={[styles.thumb, { opacity: 0.5 }]} />
            <Ionicons name="cloud-upload-outline" size={18} color={colors.warning} style={styles.badge} />
          </View>
        ))}
        {!photos.length && !queued.length && <Text style={styles.empty}>Chưa có ảnh. Chụp trước và sau khi xử lý để làm bằng chứng.</Text>}
      </ScrollView>
      {!disabled && (
        <>
          <TextInput value={caption} onChangeText={setCaption} placeholder="Chú thích (tuỳ chọn)" maxLength={300} style={styles.input} placeholderTextColor={colors.textMuted} />
          <View style={styles.row}>
            <TouchableOpacity style={styles.button} onPress={() => void pick("camera")} disabled={busy}>
              {busy ? <ActivityIndicator color="#fff" /> : <Ionicons name="camera-outline" size={18} color="#fff" />}
              <Text style={styles.buttonText}>Chụp ảnh</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.button, styles.secondary]} onPress={() => void pick("library")} disabled={busy}>
              <Ionicons name="images-outline" size={18} color={colors.primary} />
              <Text style={[styles.buttonText, { color: colors.primary }]}>Thư viện</Text>
            </TouchableOpacity>
          </View>
        </>
      )}
      {message ? <Text style={styles.message}>{message}</Text> : null}
      <Modal visible={!!viewing} transparent animationType="fade" onRequestClose={() => setViewing(null)}>
        <View style={styles.viewer}>
          {viewing && <Image source={{ uri: viewing.url }} style={styles.full} resizeMode="contain" />}
          <Text style={styles.viewerCaption}>{viewing?.caption || "Không có chú thích"}</Text>
          <View style={styles.row}>
            {viewing && viewing.uploaded_by === userId && (
              <TouchableOpacity style={[styles.button, { backgroundColor: colors.destructive }]} onPress={() => void remove(viewing)}>
                <Text style={styles.buttonText}>Xoá</Text>
              </TouchableOpacity>
            )}
            <TouchableOpacity style={[styles.button, { backgroundColor: "#ffffff33" }]} onPress={() => setViewing(null)}>
              <Text style={styles.buttonText}>Đóng</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 15, marginBottom: 11 },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  title: { color: colors.text, fontWeight: "800", fontSize: 15 },
  meta: { color: colors.textMuted, fontSize: 11 },
  thumb: { width: 84, height: 84, borderRadius: 10, backgroundColor: colors.border },
  badge: { position: "absolute", right: 4, bottom: 4 },
  empty: { color: colors.textMuted, fontSize: 12, lineHeight: 18, paddingVertical: 6 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: 10, paddingHorizontal: 11, paddingVertical: 9, color: colors.text, fontSize: 13, marginTop: 4 },
  row: { flexDirection: "row", gap: 8, marginTop: 9 },
  button: { flex: 1, flexDirection: "row", gap: 6, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary, borderRadius: 10, paddingVertical: 11 },
  secondary: { backgroundColor: colors.primarySoft },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 13 },
  message: { color: colors.warning, fontSize: 11, marginTop: 8, lineHeight: 16 },
  viewer: { flex: 1, backgroundColor: "#000000EE", justifyContent: "center", padding: 16 },
  full: { width: "100%", height: "75%" },
  viewerCaption: { color: "#fff", textAlign: "center", marginTop: 10 },
});
