import React, { useEffect, useState } from "react";
import { Alert, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useAuth } from "../lib/auth";
import { getApiUrl, setApiUrl, getErrorMessage } from "../lib/api";
import GradientButton from "../components/GradientButton";
import PersonalDataCard from "../components/PersonalDataCard";
import { colors, radius } from "../lib/theme";

export default function SettingsScreen({ navigation }: any) {
  const { user, logout } = useAuth();
  const [url, setUrl] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getApiUrl().then(setUrl);
  }, []);

  async function onSave() {
    try {
      await setApiUrl(url.trim());
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (error) {
      Alert.alert("Không lưu được máy chủ", getErrorMessage(error));
    }
  }

  return (
    <ScrollView style={styles.flex} contentContainerStyle={{ paddingBottom: 24 }}>
      <View style={styles.card}>
        <Text style={styles.label}>Tài khoản</Text>
        <Text style={styles.value}>{user?.email}</Text>
        <View style={styles.roleBadge}>
          <Text style={styles.roleText}>{user?.role}</Text>
        </View>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Địa chỉ Backend API</Text>
        <Text style={styles.hint}>
          Điện thoại và máy tính phải chung mạng Wi-Fi. Dùng địa chỉ IP LAN của máy tính, ví dụ:
          http://192.168.1.140:8000. Đổi máy chủ sẽ đăng xuất; dữ liệu offline cũ được giữ và chỉ đồng bộ khi chọn lại máy chủ gốc.
        </Text>
        <TextInput
          style={styles.input}
          value={url}
          onChangeText={setUrl}
          autoCapitalize="none"
          placeholder="http://192.168.1.140:8000"
          placeholderTextColor={colors.textMuted}
        />
        <GradientButton
          title={saved ? "Đã lưu ✓" : "Lưu địa chỉ"}
          onPress={onSave}
          style={{ marginTop: 12 }}
        />
      </View>

      <PersonalDataCard onOpenPolicy={() => navigation.navigate("Privacy")} />

      <GradientButton
        title="Đăng xuất"
        variant="outline"
        onPress={() => Alert.alert("Đăng xuất?", "", [
          { text: "Huỷ", style: "cancel" },
          { text: "Đăng xuất", style: "destructive", onPress: logout },
        ])}
        style={{ margin: 16 }}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background, padding: 16 },
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: 16,
    marginBottom: 16,
  },
  label: { fontSize: 12, fontWeight: "700", color: colors.textMuted, textTransform: "uppercase" },
  value: { fontSize: 16, fontWeight: "600", color: colors.text, marginTop: 6 },
  roleBadge: {
    alignSelf: "flex-start",
    backgroundColor: colors.secondary,
    borderRadius: radius.full,
    paddingHorizontal: 10,
    paddingVertical: 3,
    marginTop: 8,
  },
  roleText: { fontSize: 11, color: colors.text, fontWeight: "600" },
  hint: { fontSize: 12, color: colors.textMuted, marginTop: 6, marginBottom: 12, lineHeight: 17 },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
    color: colors.text,
  },
});
