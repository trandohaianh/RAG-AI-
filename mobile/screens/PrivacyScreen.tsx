import React from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { POLICY_SECTIONS, PRIVACY_CONTACT, PRIVACY_VERSION } from "../lib/privacy";
import { colors, radius } from "../lib/theme";

/** Nội dung Chính sách quyền riêng tư (dùng cả trước và sau khi đăng nhập). */
export function PolicyBody() {
  return (
    <View>
      <Text style={styles.meta}>
        Phiên bản {PRIVACY_VERSION}. Áp dụng theo Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15.
      </Text>
      <Text style={styles.intro}>
        FieldOps giúp đội kỹ thuật nhận ca, xếp lịch và ghi nhận công việc tại nhà khách. Dưới đây là dữ liệu được
        thu thập, dùng để làm gì, gửi cho ai và quyền của bạn.
      </Text>
      {POLICY_SECTIONS.map((section) => (
        <View key={section.title} style={styles.section}>
          <Text style={styles.sectionTitle}>{section.title}</Text>
          {section.items.map((item) => (
            <Text key={item} style={styles.item}>
              • {item.replace("{contact}", PRIVACY_CONTACT)}
            </Text>
          ))}
        </View>
      ))}
    </View>
  );
}

export default function PrivacyScreen() {
  return (
    <ScrollView style={styles.flex} contentContainerStyle={styles.content}>
      <View style={styles.card}>
        <PolicyBody />
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, paddingBottom: 32 },
  card: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 16 },
  meta: { color: colors.textMuted, fontSize: 12 },
  intro: { color: colors.text, fontSize: 13, lineHeight: 19, marginTop: 10 },
  section: { marginTop: 16 },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: "800", marginBottom: 6 },
  item: { color: colors.text, fontSize: 13, lineHeight: 19, marginBottom: 4 },
});
