import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, ViewStyle } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { colors, radius } from "../lib/theme";

type Props = {
  title: string;
  onPress: () => void;
  loading?: boolean;
  disabled?: boolean;
  variant?: "primary" | "outline";
  style?: ViewStyle;
  testID?: string;
};

export default function GradientButton({
  title,
  onPress,
  loading,
  disabled,
  variant = "primary",
  style,
  testID,
}: Props) {
  const isDisabled = disabled || loading;

  if (variant === "outline") {
    return (
      <TouchableOpacity
        testID={testID}
        onPress={onPress}
        disabled={isDisabled}
        style={[styles.outline, isDisabled && styles.disabled, style]}
        activeOpacity={0.7}
      >
        <Text style={styles.outlineText}>{title}</Text>
      </TouchableOpacity>
    );
  }

  return (
    <TouchableOpacity testID={testID} onPress={onPress} disabled={isDisabled} activeOpacity={0.85} style={style}>
      <LinearGradient
        colors={[colors.primary, colors.primaryTo]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[styles.button, isDisabled && styles.disabled]}
      >
        {loading ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.text}>{title}</Text>
        )}
      </LinearGradient>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: {
    height: 48,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: colors.primary,
    shadowOpacity: 0.25,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 4 },
    elevation: 3,
  },
  text: { color: "#fff", fontWeight: "700", fontSize: 15 },
  outline: {
    height: 48,
    borderRadius: radius.md,
    borderWidth: 1.5,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  outlineText: { color: colors.text, fontWeight: "600", fontSize: 15 },
  disabled: { opacity: 0.5 },
});
