import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import * as api from "../lib/api";
import {
  cacheFieldOverview,
  getCachedActiveFieldOverview,
  isSecureOfflineStorageAvailable,
  syncPendingFieldActions,
} from "../lib/fieldOffline";
import { colors, radius } from "../lib/theme";

export default function FieldHomeScreen({ navigation }: any) {
  const [workspace, setWorkspace] = useState<api.FieldWorkspace | null>(null);
  const [cases, setCases] = useState<api.FieldCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [offline, setOffline] = useState(false);

  const load = useCallback(async () => {
    const user = await api.loadUser();
    try {
      setError("");
      if (user && await isSecureOfflineStorageAvailable()) {
        const sync = await syncPendingFieldActions(user.id);
        if (sync.rejected) {
          setError(`${sync.rejected} thao tác offline bị từ chối, mở ca sửa để xem lại.`);
        } else if (sync.errors.length) {
          setError(`Đồng bộ đang chờ xử lý: ${sync.errors[0]}`);
        }
      }
      const active = await api.getActiveFieldWorkspace();
      setWorkspace(active || null);
      const latestCases = active ? await api.listFieldCases(active.id) : [];
      setCases(latestCases);
      setOffline(false);
      if (user && active && await isSecureOfflineStorageAvailable()) {
        await cacheFieldOverview(user.id, active, latestCases);
      }
    } catch (e) {
      if (user && api.isNetworkError(e) && await isSecureOfflineStorageAvailable()) {
        try {
          const cached = await getCachedActiveFieldOverview(user.id);
          if (cached) {
            setWorkspace(cached.workspace);
            setCases(cached.cases);
            setOffline(true);
            setError("Đang ngoại tuyến · hiển thị dữ liệu đã lưu an toàn trên thiết bị.");
          } else {
            setError(api.getErrorMessage(e));
          }
        } catch (cacheError) {
          setError(`${api.getErrorMessage(e)} ${api.getErrorMessage(cacheError)}`);
        }
      } else {
        setError(api.getErrorMessage(e));
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  if (loading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>;
  }

  const activeCount = cases.filter((item) => item.status !== "resolved").length;
  const waitingCount = cases.filter((item) => item.status === "waiting").length;
  const recent = cases.slice(0, 3);

  return (
    <ScrollView
      style={styles.flex}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />
      }
    >
      <View style={styles.hero}>
        <View style={styles.brandRow}>
          <View style={styles.brandIcon}><Ionicons name="construct" size={20} color="#fff" /></View>
          <Text style={styles.eyebrow}>FIELD OPERATIONS</Text>
        </View>
        <Text style={styles.heroTitle}>Ca sửa chữa,{`\n`}không chỉ là câu hỏi.</Text>
        <Text style={styles.heroSubtitle}>
          Tri thức, checklist và lịch sử xử lý cùng một chỗ cho cả đội hiện trường.
        </Text>
        <TouchableOpacity
          style={styles.heroButton}
          onPress={() => navigation.navigate("Cases", { screen: "CaseList", params: { create: true } })}
        >
          <Text style={styles.heroButtonText}>＋  Tạo ca sửa</Text>
        </TouchableOpacity>
      </View>

      {error ? <Text style={[styles.error, offline && styles.offlineNotice]}>{error}</Text> : null}

      <View style={styles.workspaceRow}>
        <View style={styles.workspaceIcon}><Ionicons name="people" size={18} color={colors.primary} /></View>
        <View style={{ flex: 1 }}>
          <Text style={styles.mutedLabel}>WORKSPACE ĐỘI</Text>
          <Text style={styles.workspaceName}>{workspace?.name || "Chưa có đội"}</Text>
        </View>
        <Text style={styles.memberCount}>{workspace?.member_count ?? 0} thành viên</Text>
      </View>

      <View style={styles.metrics}>
        <View style={styles.metricCard}>
          <Text style={styles.metricValue}>{activeCount}</Text>
          <Text style={styles.metricLabel}>Ca đang mở</Text>
        </View>
        <View style={styles.metricCard}>
          <Text style={[styles.metricValue, { color: colors.warning }]}>{waitingCount}</Text>
          <Text style={styles.metricLabel}>Chờ xử lý</Text>
        </View>
        <TouchableOpacity style={styles.metricCard} onPress={() => navigation.navigate("Team")}>
          <Ionicons name="people-outline" size={23} color={colors.primary} />
          <Text style={styles.metricLabel}>Đội kỹ thuật</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Ca gần đây</Text>
        <TouchableOpacity onPress={() => navigation.navigate("Cases")}>
          <Text style={styles.seeAll}>Xem tất cả</Text>
        </TouchableOpacity>
      </View>
      {recent.length ? recent.map((item) => (
        <TouchableOpacity
          key={item.id}
          style={styles.caseCard}
          onPress={() => navigation.navigate("Cases", { screen: "CaseDetail", params: { caseId: item.id } })}
        >
          <View style={styles.caseIcon}>
            <Ionicons name="wifi-outline" size={18} color={colors.primary} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.caseTitle} numberOfLines={1}>{item.title}</Text>
            <Text style={styles.caseSub} numberOfLines={1}>
              {item.device_model || "Chưa rõ thiết bị"} · {item.address || "Chưa có địa chỉ"}
            </Text>
          </View>
          <View style={[styles.statusDot, item.status === "resolved" && { backgroundColor: colors.success }]} />
        </TouchableOpacity>
      )) : (
        <View style={styles.emptyCard}>
          <Ionicons name="clipboard-outline" size={27} color={colors.textMuted} />
          <Text style={styles.emptyTitle}>Chưa có ca sửa nào</Text>
          <Text style={styles.emptyHint}>Tạo ca đầu tiên để bắt đầu lưu quy trình và kết quả hiện trường.</Text>
        </View>
      )}

      <Text style={styles.sectionTitle}>Hệ sinh thái</Text>
      <View style={styles.modules}>
        <Module icon="book-outline" title="Tri thức kỹ thuật" subtitle="SOP, manuals, nguồn dẫn" onPress={() => navigation.navigate("Knowledge")} />
        <Module icon="chatbubble-ellipses-outline" title="RAG Assistant" subtitle="Hỏi trên tài liệu đã nạp" onPress={() => navigation.navigate("ChatList")} />
        <Module icon="people-outline" title="Đội & phân quyền" subtitle="Đội và thành viên" onPress={() => navigation.navigate("Team")} />
      </View>
    </ScrollView>
  );
}

function Module({ icon, title, subtitle, onPress }: { icon: keyof typeof Ionicons.glyphMap; title: string; subtitle: string; onPress: () => void }) {
  return (
    <TouchableOpacity style={styles.moduleRow} onPress={onPress}>
      <Ionicons name={icon} size={20} color={colors.primary} />
      <View style={{ flex: 1, marginLeft: 12 }}>
        <Text style={styles.moduleTitle}>{title}</Text>
        <Text style={styles.moduleSubtitle}>{subtitle}</Text>
      </View>
      <Ionicons name="chevron-forward" size={17} color={colors.textMuted} />
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: colors.background },
  content: { padding: 16, paddingBottom: 32 },
  hero: { backgroundColor: "#16243A", borderRadius: 22, padding: 22, marginBottom: 16 },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 9, marginBottom: 24 },
  brandIcon: { width: 34, height: 34, borderRadius: 11, backgroundColor: "#4F46E5", alignItems: "center", justifyContent: "center" },
  eyebrow: { color: "#BFCBF0", fontWeight: "800", fontSize: 10, letterSpacing: 1.4 },
  heroTitle: { color: "#fff", fontSize: 29, lineHeight: 35, fontWeight: "800", letterSpacing: -0.6 },
  heroSubtitle: { color: "#C3CEE0", fontSize: 13, lineHeight: 19, marginTop: 10, maxWidth: 310 },
  heroButton: { backgroundColor: "#6C63FF", paddingVertical: 13, paddingHorizontal: 16, borderRadius: 12, alignSelf: "flex-start", marginTop: 20 },
  heroButtonText: { color: "#fff", fontWeight: "700", fontSize: 14 },
  error: { backgroundColor: "#FEF2F2", color: colors.destructive, borderRadius: 10, padding: 12, marginBottom: 12 },
  offlineNotice: { backgroundColor: "#FFF7ED", color: "#9A3412" },
  workspaceRow: { flexDirection: "row", alignItems: "center", backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, padding: 14, borderRadius: radius.md, marginBottom: 12 },
  workspaceIcon: { width: 38, height: 38, borderRadius: 12, backgroundColor: "#EEF2FF", alignItems: "center", justifyContent: "center", marginRight: 11 },
  mutedLabel: { color: colors.textMuted, fontWeight: "700", fontSize: 9, letterSpacing: 0.8 },
  workspaceName: { color: colors.text, fontSize: 14, fontWeight: "700", marginTop: 3 },
  memberCount: { color: colors.textMuted, fontSize: 11 },
  metrics: { flexDirection: "row", gap: 10, marginBottom: 22 },
  metricCard: { flex: 1, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 14, minHeight: 88, justifyContent: "center" },
  metricValue: { color: colors.text, fontSize: 25, fontWeight: "800" },
  metricLabel: { color: colors.textMuted, fontSize: 11, marginTop: 5 },
  sectionHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 },
  sectionTitle: { color: colors.text, fontSize: 17, fontWeight: "800", marginBottom: 10 },
  seeAll: { color: colors.primary, fontWeight: "700", fontSize: 12 },
  caseCard: { flexDirection: "row", alignItems: "center", gap: 11, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, padding: 13, borderRadius: radius.md, marginBottom: 8 },
  caseIcon: { height: 36, width: 36, borderRadius: 11, alignItems: "center", justifyContent: "center", backgroundColor: "#EEF2FF" },
  caseTitle: { color: colors.text, fontWeight: "700", fontSize: 13 },
  caseSub: { color: colors.textMuted, fontSize: 11, marginTop: 4 },
  statusDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: colors.warning },
  emptyCard: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 20, alignItems: "center", marginBottom: 22 },
  emptyTitle: { color: colors.text, fontSize: 14, fontWeight: "700", marginTop: 8 },
  emptyHint: { color: colors.textMuted, textAlign: "center", fontSize: 12, lineHeight: 17, marginTop: 5 },
  modules: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, overflow: "hidden" },
  moduleRow: { minHeight: 64, paddingHorizontal: 14, flexDirection: "row", alignItems: "center", borderBottomWidth: 1, borderBottomColor: colors.border },
  moduleTitle: { color: colors.text, fontWeight: "700", fontSize: 13 },
  moduleSubtitle: { color: colors.textMuted, fontSize: 11, marginTop: 3 },
});
