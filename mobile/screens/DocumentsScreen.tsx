import React, { useCallback, useState } from "react";
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
import * as DocumentPicker from "expo-document-picker";
import { Ionicons } from "@expo/vector-icons";
import * as api from "../lib/api";
import { getErrorMessage } from "../lib/api";
import { colors, radius } from "../lib/theme";
import GradientButton from "../components/GradientButton";

export default function DocumentsScreen() {
  const [docs, setDocs] = useState<api.DocItem[]>([]);
  const [workspaceId, setWorkspaceId] = useState<number | null>(null);
  const [workspaceRole, setWorkspaceRole] = useState<string | null>(null);
  const [scope, setScope] = useState<"team" | "personal">("team");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async () => {
    try {
      const workspace = scope === "team" ? await api.getActiveFieldWorkspace() : null;
      setWorkspaceId(workspace?.id ?? null);
      setWorkspaceRole(workspace?.role ?? null);
      const data = await api.listDocuments(workspace?.id);
      setDocs(data);
    } catch (e) {
      Alert.alert("Lỗi", getErrorMessage(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [scope]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  async function onPick() {
    const result = await DocumentPicker.getDocumentAsync({
      type: [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "image/png",
        "image/jpeg",
      ],
      copyToCacheDirectory: true,
    });
    if (result.canceled || !result.assets?.[0]) return;

    const file = result.assets[0];
    setUploading(true);
    try {
      const res = await api.uploadDocument({
        uri: file.uri,
        name: file.name,
        mimeType: file.mimeType,
      }, scope === "team" ? workspaceId ?? (await api.getActiveFieldWorkspace())?.id : undefined);
      Alert.alert("Thành công", `${res.chunks} chunks đã được lập chỉ mục`);
      load();
    } catch (e) {
      Alert.alert("Tải lên thất bại", getErrorMessage(e));
    } finally {
      setUploading(false);
    }
  }

  function onDelete(name: string) {
    Alert.alert("Xoá tài liệu?", name, [
      { text: "Huỷ", style: "cancel" },
      {
        text: "Xoá",
        style: "destructive",
        onPress: async () => {
          try {
            await api.deleteDocument(name, scope === "team" ? workspaceId ?? undefined : undefined);
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
        <View style={styles.scopeSwitch}>
          <TouchableOpacity
            style={[styles.scopeOption, scope === "team" && styles.scopeSelected]}
            onPress={() => setScope("team")}
          >
            <Text style={[styles.scopeText, scope === "team" && styles.scopeTextSelected]}>Tri thức đội</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.scopeOption, scope === "personal" && styles.scopeSelected]}
            onPress={() => setScope("personal")}
          >
            <Text style={[styles.scopeText, scope === "personal" && styles.scopeTextSelected]}>Cá nhân</Text>
          </TouchableOpacity>
        </View>
        {scope === "personal" || workspaceRole === "owner" ? (
          <GradientButton
            title={uploading ? "Đang tải lên..." : "＋ Tải tài liệu lên"}
            onPress={onPick}
            loading={uploading}
          />
        ) : (
          <Text style={styles.hint}>Chỉ trưởng đội được thêm/xóa tài liệu dùng chung.</Text>
        )}
        <Text style={styles.hint}>
          {scope === "team" ? "Tài liệu dùng chung của đội đang chọn." : "Tài liệu riêng của tài khoản."} PDF, DOCX, TXT hoặc ảnh.
        </Text>
      </View>

      <FlatList
        data={docs}
        keyExtractor={(item) => item.name}
        contentContainerStyle={{ padding: 16, paddingTop: 0, flexGrow: 1 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>
              Chưa có tài liệu {scope === "team" ? "trong tri thức đội" : "cá nhân"} — upload SOP hoặc manual để dùng trợ lý.
            </Text>
          </View>
        }
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={styles.rowIcon}>
              <Ionicons name="document-text-outline" size={18} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle} numberOfLines={1}>{item.name}</Text>
              <Text style={styles.rowSub}>{item.chunks} chunks</Text>
            </View>
            {scope === "personal" || workspaceRole === "owner" ? (
              <TouchableOpacity onPress={() => onDelete(item.name)} hitSlop={10}>
                <Ionicons name="trash-outline" size={18} color={colors.destructive} />
              </TouchableOpacity>
            ) : null}
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
  hint: { fontSize: 12, color: colors.textMuted, marginTop: 8, textAlign: "center" },
  scopeSwitch: { flexDirection: "row", backgroundColor: colors.secondary, borderRadius: radius.md, padding: 3, marginBottom: 12 },
  scopeOption: { flex: 1, alignItems: "center", paddingVertical: 9, borderRadius: 9 },
  scopeSelected: { backgroundColor: colors.card },
  scopeText: { color: colors.textMuted, fontSize: 12, fontWeight: "600" },
  scopeTextSelected: { color: colors.primary, fontWeight: "800" },
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
    backgroundColor: "#EEF2FF",
    alignItems: "center",
    justifyContent: "center",
  },
  rowTitle: { fontSize: 14, fontWeight: "600", color: colors.text },
  rowSub: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  empty: { alignItems: "center", justifyContent: "center", paddingTop: 60 },
  emptyText: { color: colors.textMuted, textAlign: "center", paddingHorizontal: 30 },
});
