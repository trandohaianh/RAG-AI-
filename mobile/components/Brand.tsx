import React from "react";
import { StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { colors, radius } from "../lib/theme";

export default function Brand({ size = 40 }: { size?: number }) {
  return (
    <View style={styles.row}>
      <LinearGradient
        colors={[colors.primary, colors.primaryTo]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[styles.logo, { width: size, height: size, borderRadius: size * 0.28 }]}
      >
        <Text style={{ fontSize: size * 0.5 }}>🛠</Text>
      </LinearGradient>
      <Text style={styles.title}>FieldOps</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10, justifyContent: "center" },
  logo: {
    alignItems: "center",
    justifyContent: "center",
    shadowColor: colors.primary,
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 3,
  },
  title: { fontSize: 22, fontWeight: "800", color: colors.primary },
});
