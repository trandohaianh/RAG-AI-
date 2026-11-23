import React, { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { useAuth } from "../lib/auth";
import { getErrorMessage } from "../lib/api";
import GradientButton from "../components/GradientButton";
import { PolicyBody } from "./PrivacyScreen";
import { colors, radius } from "../lib/theme";

/** Chưa đồng ý phiên bản chính sách hiện hành thì phải đồng ý trước khi vào app. */
export default function PrivacyConsentScreen() {
  const { user, acceptPrivacy, logout } = useAuth();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function accept() {
    setSaving(true);
    setError("");
    try {
      await acceptPrivacy();
    } catch (e) {
      setError(getErrorMessage(e));
      setSaving(false);
    }
  }

  return (
    <ScrollView style={styles.flex} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Chính sách quyền riêng tư</Text>
      <Text style={styles.lead}>
        {user?.privacy_version
          ? "Chính sách vừa được cập nhật. Vui lòng đọc và đồng ý để tiếp tục."
          : "Trước khi tiếp tục, vui lòng đọc và đồng ý Chính sách quyền riêng tư."}
      </Text>
      <View style={styles.card}>
        <PolicyBody />
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <GradientButton pill title={saving ? "Đang lưu..." : "Tôi đã đọc và đồng ý"} onPress={accept} loading={saving} />
      <GradientButton title="Đăng xuất" variant="outline" onPress={logout} style={{ marginTop: 12 }} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, paddingTop: 56, paddingBottom: 32 },
  title: { color: colors.text, fontSize: 22, fontWeight: "800" },
  lead: { color: colors.textMuted, fontSize: 13, marginTop: 6, marginBottom: 12 },
  card: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: 16,
    marginBottom: 16,
  },
  error: { color: colors.destructive, fontSize: 13, marginBottom: 10, textAlign: "center" },
});
