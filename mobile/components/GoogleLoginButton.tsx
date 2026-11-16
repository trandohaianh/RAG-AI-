import { useEffect, useState } from "react";
import { ActivityIndicator, Platform, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import * as Google from "expo-auth-session/providers/google";
import * as WebBrowser from "expo-web-browser";
import { useAuth } from "../lib/auth";
import { getErrorMessage } from "../lib/api";
import { colors, radius } from "../lib/theme";

// Đóng popup đăng nhập trên web khi Google chuyển hướng về.
WebBrowser.maybeCompleteAuthSession();

// Client ID tạo trong Google Cloud Console (OAuth client loại Web / iOS / Android).
// Backend phải liệt kê CẢ các ID này trong GOOGLE_CLIENT_ID (phân tách bằng dấu phẩy).
const WEB_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID;
const IOS_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID;
const ANDROID_CLIENT_ID = process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID;

// Hook của expo-auth-session báo lỗi nếu thiếu ID cho đúng nền tảng đang chạy → kiểm tra theo nền tảng.
export const googleLoginConfigured = Boolean(
  Platform.OS === "ios" ? IOS_CLIENT_ID : Platform.OS === "android" ? ANDROID_CLIENT_ID : WEB_CLIENT_ID
);

function GoogleLoginInner({ onError }: { onError: (message: string) => void }) {
  const { loginWithGoogle } = useAuth();
  const [busy, setBusy] = useState(false);
  const [request, response, promptAsync] = Google.useIdTokenAuthRequest({
    webClientId: WEB_CLIENT_ID,
    iosClientId: IOS_CLIENT_ID,
    androidClientId: ANDROID_CLIENT_ID,
  });

  useEffect(() => {
    if (response?.type !== "success") {
      if (response?.type === "error") onError(response.error?.message ?? "Đăng nhập Google thất bại");
      return;
    }
    const idToken = response.params?.id_token;
    if (!idToken) {
      onError("Google không trả về ID token");
      return;
    }
    setBusy(true);
    loginWithGoogle(idToken)
      .catch((err) => onError(getErrorMessage(err)))
      .finally(() => setBusy(false));
  }, [response, loginWithGoogle, onError]);

  return (
    <View style={styles.wrap}>
      <TouchableOpacity
        testID="google-login"
        style={[styles.button, (!request || busy) && styles.disabled]}
        disabled={!request || busy}
        onPress={() => void promptAsync()}
        activeOpacity={0.8}
      >
        {busy ? <ActivityIndicator color={colors.text} /> : <Text style={styles.g}>G</Text>}
        <Text style={styles.text}>Tiếp tục với Google</Text>
      </TouchableOpacity>
      <View style={styles.dividerRow}>
        <View style={styles.line} />
        <Text style={styles.divider}>hoặc dùng email</Text>
        <View style={styles.line} />
      </View>
    </View>
  );
}

/** Ẩn hoàn toàn khi chưa cấu hình client ID (hook Google bắt buộc có ID cho nền tảng đang chạy). */
export default function GoogleLoginButton({ onError }: { onError: (message: string) => void }) {
  if (!googleLoginConfigured) return null;
  return <GoogleLoginInner onError={onError} />;
}

const styles = StyleSheet.create({
  wrap: { marginTop: 4, marginBottom: 4 },
  button: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingVertical: 12,
    backgroundColor: colors.card,
  },
  disabled: { opacity: 0.5 },
  g: { fontSize: 16, fontWeight: "800", color: "#4285F4" },
  text: { fontSize: 15, fontWeight: "600", color: colors.text },
  dividerRow: { flexDirection: "row", alignItems: "center", gap: 8, marginVertical: 16 },
  line: { flex: 1, height: 1, backgroundColor: colors.border },
  divider: { fontSize: 12, color: colors.textMuted },
});
