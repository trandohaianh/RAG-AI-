import React, { useEffect, useState } from "react";
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import * as api from "../lib/api";
import { colors, radius } from "../lib/theme";
import { compactSignature } from "../lib/signature";
import GradientButton from "./GradientButton";
import SignaturePad from "./SignaturePad";

export type CloseCaseForm = Omit<api.CloseCaseInput, "client_action_id">;

type Props = {
  visible: boolean;
  item: api.FieldCase;
  busy: boolean;
  onCancel: () => void;
  /** Màn cha gửi lên máy chủ, hoặc xếp hàng offline khi mất mạng. */
  onSubmit: (form: CloseCaseForm) => void;
};

/** Biên bản đóng ca trên điện thoại (docs/design/M12_bien_ban_dong_ca.md). */
export default function CloseCaseSheet({ visible, item, busy, onCancel, onSubmit }: Props) {
  const [outcome, setOutcome] = useState("fixed");
  const [note, setNote] = useState("");
  const [customer, setCustomer] = useState(item.customer_label ?? "");
  const [strokes, setStrokes] = useState<api.SignatureStroke[]>([]);
  const [signing, setSigning] = useState(false);
  const [message, setMessage] = useState("");
  const noSignature = api.CLOSURE_OUTCOMES.find((o) => o.code === outcome)?.noSignature ?? false;

  useEffect(() => {
    if (!visible) return;
    setMessage("");
  }, [visible]);

  function submit() {
    if (note.trim().length < 3) return setMessage("Cần ghi chú kết quả (ít nhất 3 ký tự).");
    if (!noSignature && strokes.length === 0) return setMessage("Cần chữ ký của khách hàng.");
    setMessage("");
    onSubmit({
      outcome_code: outcome,
      note: note.trim(),
      customer_name: noSignature ? null : customer.trim() || null,
      signature: noSignature ? null : compactSignature(strokes),
    });
  }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onCancel}>
      <KeyboardAvoidingView style={styles.backdrop} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={styles.sheet}>
          <View style={styles.head}>
            <Text style={styles.title}>Biên bản đóng ca #{item.id}</Text>
            <TouchableOpacity onPress={onCancel} accessibilityLabel="Đóng biên bản">
              <Ionicons name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          </View>
          <ScrollView scrollEnabled={!signing} keyboardShouldPersistTaps="handled">
            <Text style={styles.label}>Kết quả *</Text>
            <View style={styles.outcomes}>
              {api.CLOSURE_OUTCOMES.map((o) => (
                <TouchableOpacity
                  key={o.code}
                  onPress={() => setOutcome(o.code)}
                  style={[styles.outcome, outcome === o.code && styles.outcomeActive]}
                  accessibilityState={{ selected: outcome === o.code }}
                >
                  <Text style={[styles.outcomeText, outcome === o.code && styles.outcomeTextActive]}>{o.label}</Text>
                </TouchableOpacity>
              ))}
            </View>

            <Text style={styles.label}>Ghi chú kết quả *</Text>
            <TextInput
              value={note}
              onChangeText={setNote}
              maxLength={2000}
              multiline
              placeholder="Đã làm gì, thay gì, tình trạng khi rời đi…"
              placeholderTextColor={colors.textMuted}
              style={[styles.input, styles.multiline]}
            />

            {noSignature ? (
              <Text style={styles.hint}>
                Không có khách để ký: ca cần ít nhất 1 ảnh hiện trường làm bằng chứng (chụp ở mục Ảnh hiện trường).
              </Text>
            ) : (
              <>
                <Text style={styles.label}>Người ký</Text>
                <TextInput
                  value={customer}
                  onChangeText={setCustomer}
                  maxLength={120}
                  placeholder="Họ tên khách hàng"
                  placeholderTextColor={colors.textMuted}
                  style={styles.input}
                />
                <View style={styles.signHead}>
                  <Text style={styles.label}>Chữ ký khách hàng *</Text>
                  <TouchableOpacity onPress={() => setStrokes([])}>
                    <Text style={styles.resign}>Ký lại</Text>
                  </TouchableOpacity>
                </View>
                <SignaturePad strokes={strokes} onChange={setStrokes} onSigningChange={setSigning} />
              </>
            )}

            {message ? (
              <Text style={styles.error} accessibilityRole="alert">
                {message}
              </Text>
            ) : null}
            <GradientButton title="Đóng ca" onPress={submit} loading={busy} style={{ marginTop: 16 }} />
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.45)" },
  sheet: { maxHeight: "92%", backgroundColor: colors.card, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 18 },
  head: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  title: { color: colors.text, fontSize: 16, fontWeight: "800" },
  label: { color: colors.text, fontSize: 12, fontWeight: "700", marginTop: 12, marginBottom: 6 },
  outcomes: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  outcome: { borderWidth: 1, borderColor: colors.border, borderRadius: 20, paddingHorizontal: 12, paddingVertical: 8 },
  outcomeActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  outcomeText: { color: colors.text, fontSize: 12, fontWeight: "600" },
  outcomeTextActive: { color: "#fff" },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10, color: colors.text, fontSize: 13 },
  multiline: { minHeight: 84, textAlignVertical: "top" },
  hint: { marginTop: 12, backgroundColor: colors.warningSoft, color: colors.text, borderRadius: 10, padding: 12, fontSize: 12, lineHeight: 18 },
  signHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end" },
  resign: { color: colors.primary, fontSize: 12, fontWeight: "700", marginBottom: 6 },
  error: { color: colors.destructive, fontSize: 12, marginTop: 12 },
});
