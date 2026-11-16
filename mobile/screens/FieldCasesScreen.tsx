import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
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
import GradientButton from "../components/GradientButton";

const statusText: Record<api.CaseStatus, string> = {
  open: "Mới",
  in_progress: "Đang xử lý",
  waiting: "Chờ khách hàng",
  escalated: "Chuyển cấp",
  resolved: "Đã hoàn tất",
};

export default function FieldCasesScreen({ navigation, route }: any) {
  const [workspace, setWorkspace] = useState<api.FieldWorkspace | null>(null);
  const [items, setItems] = useState<api.FieldCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [modal, setModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [title, setTitle] = useState("");
  const [symptom, setSymptom] = useState("");
  const [address, setAddress] = useState("");
  const [customer, setCustomer] = useState("");
  const [device, setDevice] = useState("");
  const [offline, setOffline] = useState(false);

  const load = useCallback(async (): Promise<boolean> => {
    const user = await api.loadUser();
    try {
      if (user && await isSecureOfflineStorageAvailable()) {
        await syncPendingFieldActions(user.id);
      }
      const active = await api.getActiveFieldWorkspace();
      setWorkspace(active);
      const latestItems = active ? await api.listFieldCases(active.id) : [];
      setItems(latestItems);
      setOffline(false);
      if (user && active && await isSecureOfflineStorageAvailable()) {
        await cacheFieldOverview(user.id, active, latestItems);
      }
      return true;
    } catch (e) {
      if (user && api.isNetworkError(e) && await isSecureOfflineStorageAvailable()) {
        try {
          const cached = await getCachedActiveFieldOverview(user.id);
          if (cached) {
            setWorkspace(cached.workspace);
            setItems(cached.cases);
            setOffline(true);
            return false;
          } else {
            Alert.alert("Không tải được ca sửa", api.getErrorMessage(e));
          }
        } catch (cacheError) {
          Alert.alert(
            "Không tải được ca sửa",
            `${api.getErrorMessage(e)} ${api.getErrorMessage(cacheError)}`
          );
        }
      } else {
        Alert.alert("Không tải được ca sửa", api.getErrorMessage(e));
      }
      return false;
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => {
    let focused = true;
    void load().then((online) => {
      if (!focused || !route.params?.create) return;
      if (online) {
        setModal(true);
      } else {
        Alert.alert("Cần kết nối mạng", "Tạo ca mới cần kết nối với máy chủ.");
      }
      navigation.setParams({ create: false });
    });
    return () => { focused = false; };
  }, [load, navigation, route.params?.create]));

  async function submit() {
    if (!workspace) return;
    if (title.trim().length < 3 || symptom.trim().length < 3) {
      Alert.alert("Thiếu thông tin", "Nhập tiêu đề và mô tả triệu chứng.");
      return;
    }
    setSaving(true);
    try {
      const created = await api.createFieldCase(workspace.id, {
        title: title.trim(),
        symptom: symptom.trim(),
        address: address.trim() || undefined,
        customer_label: customer.trim() || undefined,
        device_model: device.trim() || undefined,
      });
      setModal(false);
      setTitle(""); setSymptom(""); setAddress(""); setCustomer(""); setDevice("");
      await load();
      navigation.navigate("CaseDetail", { caseId: created.id });
    } catch (e) {
      Alert.alert("Không tạo được ca sửa", api.getErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>;

  return (
    <View style={styles.flex}>
      <View style={styles.top}>
        <View>
          <Text style={styles.heading}>Ca sửa chữa</Text>
          <Text style={styles.subheading}>{workspace?.name || "Workspace"}{offline ? " · Ngoại tuyến" : ""}</Text>
        </View>
        <TouchableOpacity
          style={[styles.addButton, offline && styles.addButtonDisabled]}
          onPress={() => offline
            ? Alert.alert("Cần kết nối mạng", "Tạo ca mới cần kết nối với máy chủ. Ca đã tải trước đó vẫn có thể xử lý offline.")
            : setModal(true)}
        >
          <Ionicons name="add" size={22} color="#fff" />
        </TouchableOpacity>
      </View>
      <FlatList
        data={items}
        keyExtractor={(item) => String(item.id)}
        contentContainerStyle={{ padding: 16, paddingTop: 2, flexGrow: 1 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Ionicons name="clipboard-outline" size={34} color={colors.textMuted} />
            <Text style={styles.emptyTitle}>Chưa có ca sửa</Text>
            <Text style={styles.emptyText}>Tạo ca để tập hợp triệu chứng, thiết bị, checklist và lịch sử xử lý tại hiện trường.</Text>
            <GradientButton title="＋ Tạo ca sửa đầu tiên" onPress={() => setModal(true)} style={{ marginTop: 16 }} />
          </View>
        }
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.card} onPress={() => navigation.navigate("CaseDetail", { caseId: item.id })}>
            <View style={styles.cardTop}>
              <View style={styles.deviceIcon}><Ionicons name="wifi-outline" size={20} color={colors.primary} /></View>
              <View style={{ flex: 1 }}>
                <Text style={styles.caseTitle} numberOfLines={1}>{item.title}</Text>
                <Text style={styles.caseCaption}>#{item.id} · {item.customer_label || "Khách hàng"}</Text>
              </View>
              <View style={styles.chevron}><Ionicons name="chevron-forward" size={16} color={colors.textMuted} /></View>
            </View>
            <Text style={styles.symptom} numberOfLines={2}>{item.symptom}</Text>
            <View style={styles.cardFoot}>
              <Text style={styles.deviceText}>{item.device_model || "Chưa rõ thiết bị"}</Text>
              <View style={[styles.statusPill, item.status === "resolved" && styles.donePill]}>
                <Text style={[styles.statusText, item.status === "resolved" && styles.doneText]}>{statusText[item.status]}</Text>
              </View>
            </View>
          </TouchableOpacity>
        )}
      />
      <Modal visible={modal} animationType="slide" transparent onRequestClose={() => setModal(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modal}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Tạo ca sửa hiện trường</Text>
              <TouchableOpacity onPress={() => setModal(false)}><Ionicons name="close" size={22} color={colors.textMuted} /></TouchableOpacity>
            </View>
            <Text style={styles.modalHint}>Ghi nhận thông tin ban đầu; cập nhật chi tiết và quy trình trong ca sửa.</Text>
            <TextInput style={styles.input} placeholder="Tiêu đề, ví dụ: Mất kết nối Internet" value={title} onChangeText={setTitle} placeholderTextColor={colors.textMuted} />
            <TextInput style={[styles.input, styles.multiline]} placeholder="Triệu chứng / kiểm tra từ tổng đài" value={symptom} onChangeText={setSymptom} multiline placeholderTextColor={colors.textMuted} />
            <TextInput style={styles.input} placeholder="Model modem / ONT" value={device} onChangeText={setDevice} placeholderTextColor={colors.textMuted} />
            <TextInput style={styles.input} placeholder="Khách hàng (tùy chọn)" value={customer} onChangeText={setCustomer} placeholderTextColor={colors.textMuted} />
            <TextInput style={styles.input} placeholder="Địa chỉ (tùy chọn)" value={address} onChangeText={setAddress} placeholderTextColor={colors.textMuted} />
            <GradientButton title="Tạo ca sửa" onPress={submit} loading={saving} style={{ marginTop: 4 }} />
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: colors.background },
  top: { padding: 16, paddingBottom: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  heading: { color: colors.text, fontWeight: "800", fontSize: 23 },
  subheading: { color: colors.textMuted, fontSize: 12, marginTop: 4 },
  addButton: { width: 42, height: 42, borderRadius: 14, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  addButtonDisabled: { backgroundColor: colors.textMuted },
  card: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 14, marginBottom: 10 },
  cardTop: { flexDirection: "row", alignItems: "center", gap: 10 },
  deviceIcon: { height: 40, width: 40, borderRadius: 13, backgroundColor: colors.primarySoft, alignItems: "center", justifyContent: "center" },
  caseTitle: { color: colors.text, fontSize: 14, fontWeight: "700" },
  caseCaption: { color: colors.textMuted, fontSize: 11, marginTop: 4 },
  chevron: { padding: 4 },
  symptom: { color: colors.text, fontSize: 12, lineHeight: 18, marginTop: 12 },
  cardFoot: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 12 },
  deviceText: { color: colors.textMuted, fontSize: 11 },
  statusPill: { backgroundColor: colors.warningSoft, borderRadius: 20, paddingVertical: 5, paddingHorizontal: 9 },
  statusText: { color: colors.warning, fontSize: 10, fontWeight: "700" },
  donePill: { backgroundColor: colors.successSoft },
  doneText: { color: colors.success },
  empty: { alignItems: "center", paddingHorizontal: 24, paddingTop: 82 },
  emptyTitle: { color: colors.text, fontWeight: "800", fontSize: 16, marginTop: 12 },
  emptyText: { color: colors.textMuted, textAlign: "center", fontSize: 12, lineHeight: 18, marginTop: 7 },
  modalBackdrop: { flex: 1, backgroundColor: "#11182799", justifyContent: "flex-end" },
  modal: { backgroundColor: colors.background, borderTopLeftRadius: 22, borderTopRightRadius: 22, padding: 18, paddingBottom: 30, maxHeight: "94%" },
  modalHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  modalTitle: { color: colors.text, fontWeight: "800", fontSize: 18 },
  modalHint: { color: colors.textMuted, fontSize: 12, lineHeight: 17, marginTop: 6, marginBottom: 12 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: 11, paddingHorizontal: 12, paddingVertical: 11, backgroundColor: colors.card, color: colors.text, fontSize: 13, marginBottom: 9 },
  multiline: { minHeight: 76, textAlignVertical: "top" },
});
