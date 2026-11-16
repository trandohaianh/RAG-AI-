import React, { useState } from "react";
import { View, Text, TouchableOpacity, StyleSheet } from "react-native";
import type { Evidence, CitationAudit } from "../lib/api";
import { colors } from "../lib/theme";

type Props = { evidence?: Evidence[]; audit?: CitationAudit | null; checkVersion?: (reference: string) => Promise<string> };
export default function EvidencePanel({ evidence = [], audit, checkVersion }: Props) {
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [states, setStates] = useState<Record<string, string>>({});
  if (!evidence.length) return null;
  const labels: Record<string, string> = { checking: "Đang kiểm tra…", current: "Lần kiểm tra: phiên bản chưa thay đổi",
    changed: "Kho tài liệu đã cập nhật; đây là đoạn trích lúc trả lời", unverified: "Chưa xác định được phiên bản", unavailable: "Chưa kiểm tra được phiên bản" };
  async function check(reference: string) {
    setStates(value => ({ ...value, [reference]: "checking" }));
    try { const status = await checkVersion!(reference); setStates(value => ({ ...value, [reference]: status })); }
    catch { setStates(value => ({ ...value, [reference]: "unavailable" })); }
  }
  return <View style={styles.panel}>
    <Text style={styles.title}>Đoạn trích tài liệu</Text>
    {audit?.status === "uncited" && <Text style={styles.notice}>Câu trả lời chưa dẫn đến đoạn nguồn cụ thể.</Text>}
    {evidence.map(item => <View key={item.reference_id} style={styles.card}>
      <TouchableOpacity accessibilityRole="button" accessibilityLabel={`Xem đoạn trích ${item.reference_id}`} onPress={() => setOpened(value => ({...value, [item.reference_id]: !value[item.reference_id]}))}>
        <Text style={styles.title}>【{item.reference_id}】 {item.source}{item.page ? ` · trang ${item.page}` : ""}</Text>
        {audit?.cited_references.includes(item.reference_id) && <Text style={styles.notice}>Đã dẫn</Text>}
      </TouchableOpacity>
      {opened[item.reference_id] && <View>
        <Text selectable style={styles.quote}>{item.quote}</Text>
        {checkVersion && <TouchableOpacity accessibilityRole="button" disabled={states[item.reference_id] === "checking"} onPress={() => check(item.reference_id)}><Text style={styles.link}>Kiểm tra phiên bản</Text></TouchableOpacity>}
        {states[item.reference_id] && <Text style={styles.notice}>{labels[states[item.reference_id]] || labels.unavailable}</Text>}
      </View>}
    </View>)}
    <Text style={styles.notice}>Đoạn trích được lưu lúc trả lời để bạn đối chiếu.</Text>
  </View>;
}
const styles = StyleSheet.create({ panel: {marginTop: 10, gap: 6}, card: {borderColor: colors.border, borderWidth: 1, borderRadius: 8, padding: 8},
  title: {fontSize: 12, fontWeight: "600", color: colors.text}, notice: {fontSize: 11, lineHeight: 16, color: colors.textMuted},
  quote: {fontSize: 12, lineHeight: 19, color: colors.text, marginVertical: 8}, link: {fontSize: 12, color: colors.primary, paddingVertical: 5}});
