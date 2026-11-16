import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { useAuth } from "../lib/auth";
import { getErrorMessage } from "../lib/api";
import GradientButton from "../components/GradientButton";
import GoogleLoginButton from "../components/GoogleLoginButton";
import Brand from "../components/Brand";
import { colors, radius } from "../lib/theme";

export default function LoginScreen({ navigation }: any) {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit() {
    setError("");
    setLoading(true);
    try {
      await login(email, password);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={{ marginBottom: 32 }}>
          <Brand />
        </View>

        <Text style={styles.heading}>Đăng nhập</Text>

        {error ? <Text style={styles.error}>{error}</Text> : null}

        <GoogleLoginButton onError={setError} />

        <Text style={styles.label}>Email</Text>
        <TextInput
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          keyboardType="email-address"
          placeholder="you@example.com"
          placeholderTextColor={colors.textMuted}
        />

        <Text style={styles.label}>Mật khẩu</Text>
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          placeholder="••••••••"
          placeholderTextColor={colors.textMuted}
        />

        <GradientButton pill
          title={loading ? "Đang đăng nhập..." : "Đăng nhập"}
          onPress={onSubmit}
          loading={loading}
          style={{ marginTop: 8 }}
        />

        <TouchableOpacity onPress={() => navigation.navigate("Register")} style={styles.link}>
          <Text style={styles.linkText}>
            Chưa có tài khoản? <Text style={styles.linkAccent}>Đăng ký ngay</Text>
          </Text>
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  container: { flexGrow: 1, justifyContent: "center", padding: 24 },
  heading: { fontSize: 20, fontWeight: "700", color: colors.text, marginBottom: 16, textAlign: "center" },
  label: { fontSize: 13, fontWeight: "600", color: colors.text, marginBottom: 6, marginTop: 12 },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.full,
    paddingHorizontal: 18,
    paddingVertical: 12,
    fontSize: 15,
    backgroundColor: colors.card,
    color: colors.text,
  },
  error: { color: colors.destructive, fontSize: 13, marginBottom: 8, textAlign: "center" },
  link: { marginTop: 20, alignItems: "center" },
  linkText: { color: colors.textMuted, fontSize: 14 },
  linkAccent: { color: colors.primary, fontWeight: "700" },
});
