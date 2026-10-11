import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { Ionicons } from "@expo/vector-icons";
import * as api from "../lib/api";
import { colors, radius } from "../lib/theme";
import GradientButton from "../components/GradientButton";

export default function FieldTeamScreen() {
  const [workspaces, setWorkspaces] = useState<api.FieldWorkspace[]>([]);
  const [workspace, setWorkspace] = useState<api.FieldWorkspace | null>(null);
  const [members, setMembers] = useState<api.FieldMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [modal, setModal] = useState<"member" | "workspace" | null>(null);
  const [email, setEmail] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.listFieldWorkspaces();
      setWorkspaces(data);
      const stored = await api.getActiveFieldWorkspace();
      const active = stored || data[0] || null;
      setWorkspace(active);
      setMembers(active ? await api.listFieldMembers(active.id) : []);
    } catch (e) {
      Alert.alert("Không tải được đội", api.getErrorMessage(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  async function invite() {
    if (!workspace || !email.trim()) return;
    setSaving(true);
    try {
      await api.addFieldMember(workspace.id, email.trim());
      setEmail(""); setModal(null); await load();
    } catch (e) {
      Alert.alert("Không thêm được thành viên", api.getErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function createWorkspace() {
    if (workspaceName.trim().length < 2) return;
    setSaving(true);
    try {
      await api.createFieldWorkspace(workspaceName.trim());
      setWorkspaceName(""); setModal(null); await load();
    } catch (e) {
      Alert.alert("Không tạo được đội", api.getErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>;

  return (
    <ScrollView
      style={styles.flex}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
    >
      <View style={styles.headingRow}>
        <View>
          <Text style={styles.heading}>Đội của tôi</Text>
          <Text style={styles.subtitle}>Không gian cộng tác cho ca sửa và tri thức</Text>
        </View>
        <TouchableOpacity style={styles.addButton} onPress={() => setModal("workspace")}>
          <Ionicons name="business-outline" size={19} color={colors.primary} />
        </TouchableOpacity>
      </View>

      {workspaces.map((item) => (
        <TouchableOpacity
          key={item.id}
          style={[styles.workspaceCard, workspace?.id === item.id && styles.workspaceActive]}
          onPress={async () => {
            await api.setActiveFieldWorkspace(item.id);
            setWorkspace(item);
            try { setMembers(await api.listFieldMembers(item.id)); }
            catch (e) { Alert.alert("Lỗi", api.getErrorMessage(e)); }
          }}
        >
          <View style={styles.workspaceIcon}><Ionicons name="people" size={20} color={colors.primary} /></View>
          <View style={{ flex: 1 }}>
            <Text style={styles.workspaceName}>{item.name}</Text>
            <Text style={styles.workspaceMeta}>{item.member_count} thành viên · Vai trò {item.role}</Text>
          </View>
          {workspace?.id === item.id && <Ionicons name="checkmark-circle" size={20} color={colors.success} />}
        </TouchableOpacity>
      ))}

      <View style={styles.sectionHeader}>
        <View>
          <Text style={styles.sectionTitle}>Thành viên</Text>
          <Text style={styles.subtitle}>Tài khoản đã đăng ký có thể được thêm vào đội</Text>
        </View>
        {workspace?.role === "owner" && (
          <TouchableOpacity style={styles.inviteButton} onPress={() => setModal("member")}>
            <Ionicons name="person-add-outline" size={16} color="#fff" />
            <Text style={styles.inviteText}>Thêm</Text>
          </TouchableOpacity>
        )}
      </View>

      <View style={styles.memberList}>
        {members.map((member) => (
          <View key={member.user_id} style={styles.memberRow}>
            <View style={styles.avatar}><Text style={styles.avatarText}>{member.email.slice(0, 1).toUpperCase()}</Text></View>
            <View style={{ flex: 1 }}>
              <Text style={styles.memberEmail}>{member.email}</Text>
              <Text style={styles.memberRole}>{member.role === "owner" ? "Trưởng đội" : "Kỹ thuật viên"}</Text>
            </View>
            <Ionicons name="shield-checkmark-outline" size={18} color={member.role === "owner" ? colors.primary : colors.textMuted} />
          </View>
        ))}
      </View>

      <View style={styles.notice}>
        <Ionicons name="information-circle-outline" size={19} color={colors.primary} />
        <Text style={styles.noticeText}>Thành viên cần đăng ký tài khoản trước. Chỉ thành viên trong đội mới xem được ca sửa của đội.</Text>
      </View>

      <Modal visible={modal !== null} transparent animationType="slide" onRequestClose={() => setModal(null)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modal}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>{modal === "member" ? "Thêm thành viên" : "Tạo đội"}</Text>
              <TouchableOpacity onPress={() => setModal(null)}><Ionicons name="close" size={22} color={colors.textMuted} /></TouchableOpacity>
            </View>
            {modal === "member" ? (
              <>
                <Text style={styles.modalHint}>Nhập email tài khoản đã đăng ký trong ứng dụng.</Text>
                <TextInput style={styles.input} value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="kythuat@example.com" placeholderTextColor={colors.textMuted} />
                <GradientButton title="Thêm vào đội" onPress={invite} loading={saving} />
              </>
            ) : (
              <>
                <Text style={styles.modalHint}>Tạo không gian riêng cho một đội hoặc khu vực vận hành.</Text>
                <TextInput style={styles.input} value={workspaceName} onChangeText={setWorkspaceName} placeholder="Ví dụ: Đội FTTH Quận 1" placeholderTextColor={colors.textMuted} />
                <GradientButton title="Tạo đội" onPress={createWorkspace} loading={saving} />
              </>
            )}
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: colors.background },
  content: { padding: 16, paddingBottom: 30 },
  headingRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 18 },
  heading: { color: colors.text, fontSize: 23, fontWeight: "800" },
  subtitle: { color: colors.textMuted, fontSize: 11, marginTop: 4, lineHeight: 16 },
  addButton: { width: 40, height: 40, borderRadius: 13, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  workspaceCard: { flexDirection: "row", alignItems: "center", padding: 13, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, marginBottom: 9 },
  workspaceActive: { borderColor: colors.primary },
  workspaceIcon: { width: 39, height: 39, backgroundColor: "#EEF2FF", borderRadius: 12, alignItems: "center", justifyContent: "center", marginRight: 11 },
  workspaceName: { color: colors.text, fontWeight: "700", fontSize: 13 },
  workspaceMeta: { color: colors.textMuted, fontSize: 10, marginTop: 4 },
  sectionHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 20, marginBottom: 10 },
  sectionTitle: { color: colors.text, fontSize: 17, fontWeight: "800" },
  inviteButton: { backgroundColor: colors.primary, borderRadius: 9, paddingHorizontal: 11, paddingVertical: 8, flexDirection: "row", alignItems: "center", gap: 5 },
  inviteText: { color: "#fff", fontSize: 11, fontWeight: "700" },
  memberList: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, overflow: "hidden" },
  memberRow: { minHeight: 62, paddingHorizontal: 13, flexDirection: "row", alignItems: "center", borderBottomWidth: 1, borderBottomColor: colors.border },
  avatar: { width: 34, height: 34, borderRadius: 17, backgroundColor: "#EDE9FE", alignItems: "center", justifyContent: "center", marginRight: 10 },
  avatarText: { color: colors.primary, fontWeight: "800" },
  memberEmail: { color: colors.text, fontSize: 12, fontWeight: "700" },
  memberRole: { color: colors.textMuted, fontSize: 10, marginTop: 4 },
  notice: { flexDirection: "row", alignItems: "flex-start", gap: 8, backgroundColor: "#EEF2FF", borderRadius: 12, padding: 12, marginTop: 13 },
  noticeText: { color: "#3730A3", fontSize: 11, lineHeight: 16, flex: 1 },
  modalBackdrop: { flex: 1, backgroundColor: "#11182799", justifyContent: "flex-end" },
  modal: { backgroundColor: colors.background, borderTopLeftRadius: 22, borderTopRightRadius: 22, padding: 18, paddingBottom: 28 },
  modalHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  modalTitle: { color: colors.text, fontWeight: "800", fontSize: 18 },
  modalHint: { color: colors.textMuted, fontSize: 12, lineHeight: 17, marginVertical: 12 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: 11, paddingHorizontal: 12, paddingVertical: 11, backgroundColor: colors.card, color: colors.text, fontSize: 13, marginBottom: 12 },
});
