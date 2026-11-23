import React, { useState } from "react";
import { Alert, Platform, Share, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as api from "../lib/api";
import { useAuth } from "../lib/auth";
import GradientButton from "./GradientButton";
import { colors, radius } from "../lib/theme";

/** Cài đặt → Dữ liệu cá nhân: chính sách, tải dữ liệu của tôi, xoá tài khoản (bước 7, P1). */
export default function PersonalDataCard({ onOpenPolicy }: { onOpenPolicy: () => void }) {
  const { deleteAccount } = useAuth();
  const [exporting, setExporting] = useState(false);
  const [preview, setPreview] = useState<api.DeletePreview | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function exportData() {
    setExporting(true);
    setError("");
    try {
      const text = JSON.stringify(await api.exportMyData(), null, 2);
      if (Platform.OS === "web") {
        const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = `fieldops-du-lieu-cua-toi-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        await Share.share({ title: "Dữ liệu của tôi (FieldOps)", message: text });
      }
    } catch (e) {
      setError(api.getErrorMessage(e));
    } finally {
      setExporting(false);
    }
  }

  async function openDelete() {
    setError("");
    try {
      setPreview(await api.getDeletePreview());
    } catch (e) {
      setError(api.getErrorMessage(e));
    }
  }

  function confirmDelete() {
    if (!preview) return;
    Alert.alert("Xoá vĩnh viễn tài khoản?", "Không thể hoàn tác.", [
      { text: "Huỷ", style: "cancel" },
      {
        text: "Xoá",
        style: "destructive",
        onPress: async () => {
          setBusy(true);
          setError("");
          try {
            await deleteAccount(confirm, preview.requires_password ? password : null);
          } catch (e) {
            setError(api.getErrorMessage(e));
            setBusy(false);
          }
        },
      },
    ]);
  }

  const blocked = (preview?.teams_blocked.length ?? 0) > 0;
  const ready =
    !!preview &&
    !blocked &&
    confirm.trim().toUpperCase() === preview.confirm_word &&
    (!preview.requires_password || password.length > 0);

  return (
    <View style={styles.card}>
      <Text style={styles.label}>Dữ liệu cá nhân</Text>
      <TouchableOpacity onPress={onOpenPolicy} accessibilityRole="link">
        <Text style={styles.link}>Xem Chính sách quyền riêng tư</Text>
      </TouchableOpacity>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <GradientButton
        title={exporting ? "Đang chuẩn bị..." : "Tải dữ liệu của tôi"}
        variant="outline"
        onPress={exportData}
        loading={exporting}
        style={{ marginTop: 12 }}
      />
      {!preview ? (
        <TouchableOpacity onPress={openDelete} style={styles.deleteLink} accessibilityRole="button">
          <Text style={styles.deleteText}>Xoá tài khoản</Text>
        </TouchableOpacity>
      ) : (
        <View style={styles.deleteBox}>
          <Text style={styles.warnTitle}>Xoá tài khoản không thể hoàn tác.</Text>
          <Text style={styles.item}>• Email, hội thoại, tài liệu cá nhân và thông báo bị xoá; bạn bị đăng xuất khỏi mọi máy.</Text>
          {preview.teams_deleted.map((t) => (
            <Text key={t.id} style={styles.item}>
              • Đội {t.name} chỉ có mình bạn: đội và {t.cases} ca (kèm ảnh, chữ ký) bị xoá hẳn.
            </Text>
          ))}
          {preview.teams_left.map((t) => (
            <Text key={t.id} style={styles.item}>
              • Bạn rời đội {t.name}; ca của đội vẫn còn.
            </Text>
          ))}
          {blocked ? (
            <Text style={styles.blocked}>
              Bạn là trưởng đội duy nhất của {preview.teams_blocked.map((t) => t.name).join(", ")}. Hãy chuyển quyền
              trưởng đội cho người khác trước.
            </Text>
          ) : (
            <>
              {preview.requires_password ? (
                <TextInput
                  style={styles.input}
                  value={password}
                  onChangeText={setPassword}
                  secureTextEntry
                  placeholder="Mật khẩu hiện tại"
                  placeholderTextColor={colors.textMuted}
                />
              ) : null}
              <TextInput
                style={styles.input}
                value={confirm}
                onChangeText={setConfirm}
                autoCapitalize="characters"
                placeholder={`Gõ ${preview.confirm_word} để xác nhận`}
                placeholderTextColor={colors.textMuted}
              />
            </>
          )}
          <View style={styles.row}>
            <TouchableOpacity onPress={() => setPreview(null)} disabled={busy} style={styles.cancel}>
              <Text style={styles.cancelText}>Huỷ</Text>
            </TouchableOpacity>
            {!blocked ? (
              <TouchableOpacity
                onPress={confirmDelete}
                disabled={!ready || busy}
                style={[styles.danger, (!ready || busy) && { opacity: 0.4 }]}
              >
                <Text style={styles.dangerText}>{busy ? "Đang xoá..." : "Xoá vĩnh viễn"}</Text>
              </TouchableOpacity>
            ) : null}
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 16,
    marginBottom: 16,
  },
  label: { fontSize: 12, fontWeight: "700", color: colors.textMuted, textTransform: "uppercase" },
  link: { color: colors.primary, fontWeight: "700", marginTop: 8 },
  error: { color: colors.destructive, fontSize: 13, marginTop: 8 },
  deleteLink: { marginTop: 14, alignSelf: "flex-start" },
  deleteText: { color: colors.destructive, fontWeight: "700" },
  deleteBox: { marginTop: 14, borderWidth: 1, borderColor: colors.destructive, borderRadius: radius.md, padding: 12 },
  warnTitle: { color: colors.destructive, fontWeight: "800", marginBottom: 6 },
  item: { color: colors.text, fontSize: 12, lineHeight: 18 },
  blocked: { color: colors.warningText, backgroundColor: colors.warningSoft, borderRadius: 8, padding: 10, marginTop: 8, fontSize: 12 },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginTop: 8,
    color: colors.text,
  },
  row: { flexDirection: "row", justifyContent: "flex-end", gap: 10, marginTop: 12 },
  cancel: { paddingHorizontal: 14, paddingVertical: 9 },
  cancelText: { color: colors.textMuted, fontWeight: "700" },
  danger: { backgroundColor: colors.destructive, borderRadius: 10, paddingHorizontal: 14, paddingVertical: 9 },
  dangerText: { color: "#fff", fontWeight: "800" },
});
