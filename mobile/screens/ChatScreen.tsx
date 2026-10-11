import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import * as api from "../lib/api";
import { colors, radius } from "../lib/theme";

type ChatMsg = {
  id?: number;
  role: "user" | "assistant";
  content: string;
  sources?: string[];
  streaming?: boolean;
  rating?: number | null;
  eval_faithfulness?: number | null;
  eval_relevancy?: number | null;
};

function scoreColor(score: number) {
  if (score >= 0.75) return colors.success;
  if (score >= 0.4) return colors.warning;
  return colors.destructive;
}

export default function ChatScreen({ route, navigation }: any) {
  const { convId, title } = route.params as { convId: number; title: string };
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [docs, setDocs] = useState<api.DocItem[]>([]);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [pickerVisible, setPickerVisible] = useState(false);
  const listRef = useRef<FlatList>(null);

  useEffect(() => {
    api.listDocuments().then(setDocs).catch(() => {});
  }, []);

  useEffect(() => {
    navigation.setOptions({ title: title || "Chat" });
  }, [navigation, title]);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.getMessages(convId);
        setMessages(
          data.map((m) => ({
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.content,
            sources: m.sources,
            rating: m.rating,
            eval_faithfulness: m.eval_faithfulness,
            eval_relevancy: m.eval_relevancy,
          }))
        );
      } catch {
        /* ignore */
      } finally {
        setInitialLoading(false);
      }
    })();
  }, [convId]);

  const scrollToEnd = useCallback(() => {
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 60);
  }, []);

  const pollEvalScore = useCallback(
    async (messageId: number) => {
      for (let attempt = 0; attempt < 4; attempt++) {
        await new Promise((r) => setTimeout(r, 2500));
        try {
          const data = await api.getMessages(convId);
          const found = data.find((m) => m.id === messageId);
          if (found && found.eval_faithfulness != null) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === messageId
                  ? { ...m, eval_faithfulness: found.eval_faithfulness, eval_relevancy: found.eval_relevancy }
                  : m
              )
            );
            return;
          }
        } catch {
          /* ignore */
        }
      }
    },
    [convId]
  );

  function send(text: string) {
    const question = text.trim();
    if (!question || loading) return;
    setInput("");
    setLoading(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "", streaming: true },
    ]);
    scrollToEnd();

    api.streamChat(convId, question, {
      onToken: (token) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            next[next.length - 1] = { ...last, content: last.content + token, streaming: true };
          }
          return next;
        });
      },
      onDone: (sources, messageId) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            next[next.length - 1] = {
              ...last,
              sources,
              streaming: false,
              id: messageId ?? undefined,
              rating: null,
            };
          }
          return next;
        });
        setLoading(false);
        scrollToEnd();
        if (messageId) pollEvalScore(messageId);
      },
      onError: (err) => {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { role: "assistant", content: `Lỗi: ${err}`, streaming: false };
          return next;
        });
        setLoading(false);
      },
    }, selectedSource);
  }

  async function rate(messageId: number, rating: -1 | 0 | 1) {
    setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, rating } : m)));
    try {
      await api.sendFeedback(convId, messageId, rating);
    } catch {
      /* bỏ qua — không chặn luồng chat */
    }
  }

  function renderItem({ item }: { item: ChatMsg }) {
    const isUser = item.role === "user";
    return (
      <View style={[styles.rowWrap, isUser ? styles.rowRight : styles.rowLeft]}>
        {isUser ? (
          <LinearGradient
            colors={[colors.primary, colors.primaryTo]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={[styles.bubble, styles.bubbleUser]}
          >
            <Text style={styles.bubbleTextUser}>{item.content}</Text>
          </LinearGradient>
        ) : (
          <View style={[styles.bubble, styles.bubbleAssistant]}>
            {item.streaming && !item.content ? (
              <ActivityIndicator size="small" color={colors.textMuted} />
            ) : (
              <Text style={styles.bubbleTextAssistant}>{item.content}</Text>
            )}

            {(item.eval_faithfulness != null || item.eval_relevancy != null) && !item.streaming && (
              <View style={styles.badgeRow}>
                {item.eval_faithfulness != null && (
                  <View style={[styles.badge, { borderColor: scoreColor(item.eval_faithfulness) }]}>
                    <Text style={[styles.badgeText, { color: scoreColor(item.eval_faithfulness) }]}>
                      Bám sát nguồn {Math.round(item.eval_faithfulness * 100)}%
                    </Text>
                  </View>
                )}
                {item.eval_relevancy != null && (
                  <View style={[styles.badge, { borderColor: scoreColor(item.eval_relevancy) }]}>
                    <Text style={[styles.badgeText, { color: scoreColor(item.eval_relevancy) }]}>
                      Đúng trọng tâm {Math.round(item.eval_relevancy * 100)}%
                    </Text>
                  </View>
                )}
              </View>
            )}

            {item.id && !item.streaming && (
              <View style={styles.feedbackRow}>
                <TouchableOpacity
                  testID="thumbs-up"
                  onPress={() => rate(item.id!, item.rating === 1 ? 0 : 1)}
                  hitSlop={8}
                  style={[styles.feedbackBtn, item.rating === 1 && { backgroundColor: "#ECFDF5" }]}
                >
                  <Ionicons
                    name="thumbs-up-outline"
                    size={15}
                    color={item.rating === 1 ? colors.success : colors.textMuted}
                  />
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={() => rate(item.id!, item.rating === -1 ? 0 : -1)}
                  hitSlop={8}
                  style={[styles.feedbackBtn, item.rating === -1 && { backgroundColor: "#FEF2F2" }]}
                >
                  <Ionicons
                    name="thumbs-down-outline"
                    size={15}
                    color={item.rating === -1 ? colors.destructive : colors.textMuted}
                  />
                </TouchableOpacity>
              </View>
            )}
          </View>
        )}
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={90}
    >
      {initialLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : (
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(_, i) => String(i)}
          renderItem={renderItem}
          contentContainerStyle={{ padding: 14 }}
          onContentSizeChange={scrollToEnd}
        />
      )}

      <TouchableOpacity style={styles.sourceBar} onPress={() => setPickerVisible(true)}>
        <Ionicons name="document-text-outline" size={14} color={colors.primary} />
        <Text style={styles.sourceBarText} numberOfLines={1}>
          Đang hỏi: {selectedSource ?? "Tất cả tài liệu"}
        </Text>
        <Ionicons name="chevron-down" size={14} color={colors.textMuted} />
      </TouchableOpacity>

      <Modal visible={pickerVisible} transparent animationType="fade" onRequestClose={() => setPickerVisible(false)}>
        <TouchableOpacity style={styles.modalBackdrop} activeOpacity={1} onPress={() => setPickerVisible(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Chọn phạm vi tài liệu</Text>
            <TouchableOpacity
              style={styles.modalRow}
              onPress={() => {
                setSelectedSource(null);
                setPickerVisible(false);
              }}
            >
              <Ionicons
                name={selectedSource === null ? "radio-button-on" : "radio-button-off"}
                size={18}
                color={colors.primary}
              />
              <Text style={styles.modalRowText}>Tất cả tài liệu</Text>
            </TouchableOpacity>
            {docs.map((d) => (
              <TouchableOpacity
                key={d.name}
                style={styles.modalRow}
                onPress={() => {
                  setSelectedSource(d.name);
                  setPickerVisible(false);
                }}
              >
                <Ionicons
                  name={selectedSource === d.name ? "radio-button-on" : "radio-button-off"}
                  size={18}
                  color={colors.primary}
                />
                <Text style={styles.modalRowText} numberOfLines={1}>{d.name}</Text>
                <Text style={styles.modalRowSub}>{d.chunks} chunks</Text>
              </TouchableOpacity>
            ))}
            {docs.length === 0 && (
              <Text style={styles.modalEmpty}>Chưa có tài liệu — upload ở tab Tài liệu</Text>
            )}
          </View>
        </TouchableOpacity>
      </Modal>

      <View style={styles.inputBar}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="Nhập câu hỏi..."
          placeholderTextColor={colors.textMuted}
          multiline
          editable={!loading}
        />
        <TouchableOpacity
          testID="send-button"
          onPress={() => send(input)}
          disabled={loading || !input.trim()}
        >
          <LinearGradient
            colors={[colors.primary, colors.primaryTo]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={[styles.sendBtn, (loading || !input.trim()) && { opacity: 0.5 }]}
          >
            <Ionicons name="send" size={18} color="#fff" />
          </LinearGradient>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  rowWrap: { marginBottom: 12, flexDirection: "row" },
  rowRight: { justifyContent: "flex-end" },
  rowLeft: { justifyContent: "flex-start" },
  bubble: { maxWidth: "85%", borderRadius: radius.lg, padding: 12 },
  bubbleUser: { borderBottomRightRadius: 4 },
  bubbleAssistant: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderBottomLeftRadius: 4,
  },
  bubbleTextUser: { color: "#fff", fontSize: 15, lineHeight: 21 },
  bubbleTextAssistant: { color: colors.text, fontSize: 15, lineHeight: 21 },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  badge: { borderWidth: 1, borderRadius: radius.full, paddingHorizontal: 8, paddingVertical: 3 },
  badgeText: { fontSize: 10, fontWeight: "700" },
  feedbackRow: { flexDirection: "row", gap: 4, marginTop: 8 },
  feedbackBtn: { padding: 5, borderRadius: radius.sm },
  inputBar: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 8,
    padding: 12,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.card,
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 15,
    maxHeight: 100,
    color: colors.text,
  },
  sendBtn: { width: 44, height: 44, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  sourceBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.card,
  },
  sourceBarText: { flex: 1, fontSize: 12, fontWeight: "600", color: colors.text },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-end" },
  modalSheet: {
    backgroundColor: colors.card,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    padding: 16,
    maxHeight: "70%",
  },
  modalTitle: { fontSize: 14, fontWeight: "700", color: colors.text, marginBottom: 10 },
  modalRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10 },
  modalRowText: { flex: 1, fontSize: 14, color: colors.text },
  modalRowSub: { fontSize: 11, color: colors.textMuted },
  modalEmpty: { fontSize: 12, color: colors.textMuted, paddingVertical: 10 },
});
