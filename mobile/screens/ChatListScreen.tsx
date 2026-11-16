import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import * as api from "../lib/api";
import { getErrorMessage } from "../lib/api";
import { colors, radius } from "../lib/theme";
import GradientButton from "../components/GradientButton";

export default function ChatListScreen({ navigation }: any) {
  const [items, setItems] = useState<api.Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.listConversations();
      setItems(data);
    } catch (e) {
      Alert.alert("Lỗi", getErrorMessage(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  async function onNewChat() {
    try {
      const conv = await api.createConversation("Cuộc trò chuyện mới");
      navigation.navigate("ChatDetail", { convId: conv.id, title: conv.title });
    } catch (e) {
      Alert.alert("Lỗi", getErrorMessage(e));
    }
  }

  function onDelete(id: number) {
    Alert.alert("Xoá hội thoại?", "Không thể hoàn tác.", [
      { text: "Huỷ", style: "cancel" },
      {
        text: "Xoá",
        style: "destructive",
        onPress: async () => {
          try {
            await api.deleteConversation(id);
            load();
          } catch (e) {
            Alert.alert("Lỗi", getErrorMessage(e));
          }
        },
      },
    ]);
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.flex}>
      <View style={{ padding: 16 }}>
        <GradientButton title="＋ Chat mới" onPress={onNewChat} />
      </View>

      <FlatList
        data={items}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={{ padding: 16, paddingTop: 0, flexGrow: 1 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>Chưa có hội thoại nào — bấm "Chat mới" để bắt đầu</Text>
          </View>
        }
        renderItem={({ item }) => (
          <TouchableOpacity
            style={styles.row}
            onPress={() => navigation.navigate("ChatDetail", { convId: item.id, title: item.title })}
            onLongPress={() => onDelete(item.id)}
          >
            <View style={styles.rowIcon}>
              <Ionicons name="chatbubble-ellipses-outline" size={18} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle} numberOfLines={1}>{item.title}</Text>
              <Text style={styles.rowDate}>{new Date(item.created_at).toLocaleDateString("vi-VN")}</Text>
            </View>
            <TouchableOpacity onPress={() => onDelete(item.id)} hitSlop={10}>
              <Ionicons name="trash-outline" size={18} color={colors.destructive} />
            </TouchableOpacity>
          </TouchableOpacity>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.card,
    borderRadius: radius.md,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: colors.border,
  },
  rowIcon: {
    width: 34,
    height: 34,
    borderRadius: radius.sm,
    backgroundColor: colors.primarySoft,
    alignItems: "center",
    justifyContent: "center",
  },
  rowTitle: { fontSize: 15, fontWeight: "600", color: colors.text },
  rowDate: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  empty: { alignItems: "center", justifyContent: "center", paddingTop: 60 },
  emptyText: { color: colors.textMuted, textAlign: "center", paddingHorizontal: 30 },
});
