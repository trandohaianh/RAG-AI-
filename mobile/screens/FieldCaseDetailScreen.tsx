import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
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
import {
  applyPendingFieldAction,
  cacheFieldCase,
  discardRejectedFieldAction,
  enqueueFieldAction,
  getCachedActiveFieldOverview,
  getCachedFieldCase,
  getPendingFieldActionCount,
  getRejectedFieldActions,
  isSecureOfflineStorageAvailable,
  prepareFieldAction,
  removeCachedFieldCase,
  retryRejectedFieldAction,
  syncPendingFieldActions,
  type PendingFieldAction,
  type RejectedFieldAction,
} from "../lib/fieldOffline";
import { markdownToPlain } from "../lib/text";
import { colors, radius } from "../lib/theme";
import GradientButton from "../components/GradientButton";

const statusOptions: { value: api.CaseStatus; label: string }[] = [
  { value: "in_progress", label: "Đang xử lý" },
  { value: "waiting", label: "Chờ khách" },
  { value: "escalated", label: "Chuyển cấp" },
  { value: "resolved", label: "Hoàn tất" },
];

export default function FieldCaseDetailScreen({ route, navigation }: any) {
  const { caseId } = route.params as { caseId: number };
  const [item, setItem] = useState<api.FieldCase | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<"step" | "note" | "assistant" | null>(null);
  const [stepTitle, setStepTitle] = useState("");
  const [stepInstruction, setStepInstruction] = useState("");
  const [stepSource, setStepSource] = useState("");
  const [note, setNote] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ answer: string; sources: string[] } | null>(null);
  const [userId, setUserId] = useState<number | null>(null);
  const [offline, setOffline] = useState(false);
  const [pendingActions, setPendingActions] = useState(0);
  const [rejectedActions, setRejectedActions] = useState<RejectedFieldAction[]>([]);
  const [syncMessage, setSyncMessage] = useState("");
  const [loadError, setLoadError] = useState("");

  const load = useCallback(async () => {
    let user: api.User | null = null;
    try {
      user = await api.loadUser();
      setUserId(user?.id ?? null);
      if (user && await isSecureOfflineStorageAvailable()) {
        const sync = await syncPendingFieldActions(user.id);
        setPendingActions(sync.pending);
        setSyncMessage(sync.errors[0] ?? "");
        setRejectedActions(await getRejectedFieldActions(user.id, caseId));
      }
      const latest = await api.getFieldCase(caseId);
      setItem(latest);
      setOffline(false);
      setLoadError("");
      if (user && await isSecureOfflineStorageAvailable()) {
        try {
          await cacheFieldCase(user.id, latest.workspace_id, latest);
          setPendingActions(await getPendingFieldActionCount(user.id));
        } catch (cacheError) {
          setSyncMessage(`Ca đã tải nhưng chưa lưu được cache offline: ${api.getErrorMessage(cacheError)}`);
        }
      }
    } catch (e) {
      if (user && api.isNetworkError(e) && await isSecureOfflineStorageAvailable()) {
        try {
          const individual = await getCachedFieldCase(user.id, caseId);
          const overview = individual
            ? null
            : await getCachedActiveFieldOverview(user.id);
          const cached = individual ?? overview?.cases.find((fieldCase) => fieldCase.id === caseId) ?? null;
          if (cached) {
            setItem(cached);
            setOffline(true);
            setLoadError("");
            setPendingActions(await getPendingFieldActionCount(user.id));
            try {
              await cacheFieldCase(user.id, cached.workspace_id, cached);
            } catch (cacheError) {
              setSyncMessage(`Đang dùng bản offline hiện có; không cập nhật được cache: ${api.getErrorMessage(cacheError)}`);
            }
          } else {
            const status = api.getApiErrorStatus(e);
            if (user && (status === 403 || status === 404)) {
              setItem(null);
              setOffline(false);
              if (await isSecureOfflineStorageAvailable()) {
                try {
                  await removeCachedFieldCase(user.id, caseId);
                } catch (cacheError) {
                  setSyncMessage(`Không xóa được cache ca đã bị thu hồi: ${api.getErrorMessage(cacheError)}`);
                }
              }
            }
            setLoadError(api.getErrorMessage(e));
          }
        } catch (cacheError) {
          setLoadError(`${api.getErrorMessage(e)} ${api.getErrorMessage(cacheError)}`);
        }
      } else {
        setLoadError(api.getErrorMessage(e));
      }
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  async function resolveRejected(action: RejectedFieldAction, retry: boolean) {
    if (!userId) return;
    try {
      if (retry) {
        await retryRejectedFieldAction(userId, action.id);
      } else {
        await discardRejectedFieldAction(userId, action.id);
      }
      setRejectedActions(await getRejectedFieldActions(userId, caseId));
      if (retry) await load();
    } catch (e) {
      Alert.alert("Không xử lý được thao tác", api.getErrorMessage(e));
    }
  }

  function confirmDiscard(action: RejectedFieldAction) {
    Alert.alert(
      "Bỏ thao tác này?",
      "Thao tác sẽ bị xóa khỏi thiết bị và không được gửi lên workspace.",
      [
        { text: "Giữ lại", style: "cancel" },
        { text: "Bỏ", style: "destructive", onPress: () => resolveRejected(action, false) },
      ]
    );
  }

  const rejectedSection = rejectedActions.length ? (
    <View style={styles.rejectedCard}>
      <Text style={styles.rejectedTitle}>{rejectedActions.length} thao tác offline cần xem lại</Text>
      <Text style={styles.syncHint}>Máy chủ đã từ chối các thao tác này nên chúng không được tự động gửi lại.</Text>
      {rejectedActions.map((action) => (
        <View key={action.id} style={styles.rejectedRow}>
          <Text style={styles.eventText}>{describeAction(action)}</Text>
          <Text style={styles.rejectedError}>{action.error}</Text>
          <View style={styles.rejectedButtons}>
            <TouchableOpacity onPress={() => resolveRejected(action, true)}>
              <Text style={styles.rejectedRetry}>Thử lại</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => confirmDiscard(action)}>
              <Text style={styles.rejectedDiscard}>Bỏ</Text>
            </TouchableOpacity>
          </View>
        </View>
      ))}
    </View>
  ) : null;

  async function saveOfflineAction(
    action: PendingFieldAction
  ) {
    if (!userId || !item) {
      throw new Error("Không xác định được người dùng để lưu thao tác offline.");
    }
    if (!(await isSecureOfflineStorageAvailable())) {
      throw new Error("Lưu thao tác offline an toàn chỉ hỗ trợ trên iOS/Android.");
    }
    const queued = await enqueueFieldAction(userId, action);
    const updated = applyPendingFieldAction(item, queued, userId);
    setItem(updated);
    setOffline(true);
    setPendingActions(await getPendingFieldActionCount(userId));
    try {
      await cacheFieldCase(userId, item.workspace_id, updated);
    } catch (error) {
      setSyncMessage(
        `Thao tác đã xếp hàng an toàn nhưng chưa cập nhật được bản ca offline: ${api.getErrorMessage(error)}`
      );
    }
  }

  async function changeStatus(status: api.CaseStatus) {
    setBusy(true);
    const action = prepareFieldAction({ kind: "status", case_id: caseId, status });
    try {
      await api.updateFieldCase(caseId, { status, client_action_id: action.id });
      await load();
    } catch (e) {
      if (api.isNetworkError(e)) {
        try {
          await saveOfflineAction(action);
        } catch (queueError) {
          Alert.alert("Không cập nhật được trạng thái", api.getErrorMessage(queueError));
        }
      } else {
        Alert.alert("Không cập nhật được trạng thái", api.getErrorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }

  async function toggleStep(step: api.FieldStep) {
    const action = prepareFieldAction({
      kind: "step_update",
      case_id: caseId,
      step_id: step.id,
      status: step.status === "done" ? "todo" : "done",
      result_note: step.status === "done" ? undefined : "Kỹ thuật viên xác nhận hoàn tất",
    });
    try {
      await api.updateFieldCaseStep(caseId, step.id, {
        status: action.status,
        result_note: action.kind === "step_update" ? action.result_note : undefined,
        client_action_id: action.id,
      });
      await load();
    } catch (e) {
      if (api.isNetworkError(e)) {
        try {
          await saveOfflineAction(action);
        } catch (queueError) {
          Alert.alert("Không cập nhật được checklist", api.getErrorMessage(queueError));
        }
      } else {
        Alert.alert("Không cập nhật được checklist", api.getErrorMessage(e));
      }
    }
  }

  async function saveStep() {
    if (!stepTitle.trim()) {
      Alert.alert("Thiếu tên bước", "Nhập nội dung checklist trước khi lưu.");
      return;
    }
    setBusy(true);
    try {
      await api.addFieldCaseStep(caseId, {
        title: stepTitle.trim(),
        instruction: stepInstruction.trim(),
        source: stepSource.trim() || undefined,
      });
      setStepTitle(""); setStepInstruction(""); setStepSource(""); setModal(null);
      await load();
    } catch (e) {
      Alert.alert("Không thêm được bước", api.getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveNote() {
    if (!note.trim()) return;
    setBusy(true);
    const action = prepareFieldAction({
      kind: "note",
      case_id: caseId,
      detail: note.trim(),
    });
    try {
      await api.addFieldCaseNote(caseId, action.detail, action.id);
      setNote(""); setModal(null); await load();
    } catch (e) {
      if (api.isNetworkError(e)) {
        try {
          await saveOfflineAction(action);
          setNote("");
          setModal(null);
        } catch (queueError) {
          Alert.alert("Không lưu được ghi chú", api.getErrorMessage(queueError));
        }
      } else {
        Alert.alert("Không lưu được ghi chú", api.getErrorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }

  async function askAssistant() {
    if (!question.trim()) return;
    setBusy(true);
    setAnswer(null);
    try {
      setAnswer(await api.askFieldAssistant(caseId, question.trim()));
    } catch (e) {
      Alert.alert("Trợ lý chưa trả lời được", api.getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>;
  }
  if (!item) {
    return (
      <View style={styles.center}>
        <Text style={styles.loadError}>{loadError || "Không tải được ca sửa."}</Text>
        {pendingActions > 0 ? (
          <Text style={styles.pendingWarning}>
            {pendingActions} thao tác offline vẫn được giữ an toàn nhưng chưa đồng bộ được.
          </Text>
        ) : null}
        {rejectedSection ? <View style={styles.rejectedWrap}>{rejectedSection}</View> : null}
        <TouchableOpacity onPress={load} style={styles.retryButton}>
          <Text style={styles.retryText}>Thử lại</Text>
        </TouchableOpacity>
      </View>
    );
  }
  const completedSteps = item.steps.filter((step) => step.status === "done").length;
  const isResolved = item.status === "resolved";

  return (
    <View style={styles.flex}>
      <ScrollView contentContainerStyle={styles.content}>
        {offline || pendingActions > 0 || syncMessage ? (
          <TouchableOpacity
            style={[styles.syncBanner, offline && styles.offlineBanner]}
            onPress={load}
          >
            <Ionicons
              name={offline ? "cloud-offline-outline" : "cloud-upload-outline"}
              size={18}
              color={offline ? "#9A3412" : colors.primary}
            />
            <View style={{ flex: 1 }}>
              <Text style={styles.syncTitle}>
                {offline ? "Đang làm việc ngoại tuyến" : `${pendingActions} thao tác chờ đồng bộ`}
              </Text>
              <Text style={styles.syncHint}>
                {syncMessage || (offline
                  ? "Thay đổi được lưu an toàn trên thiết bị và sẽ đồng bộ khi mạng hoạt động."
                  : "Chạm để thử đồng bộ với workspace.")}
              </Text>
            </View>
            <Ionicons name="sync" size={18} color={offline ? "#9A3412" : colors.primary} />
          </TouchableOpacity>
        ) : null}
        {rejectedSection}
        <View style={styles.hero}>
          <View style={styles.heroTop}>
            <View style={styles.caseId}><Text style={styles.caseIdText}>CA #{item.id}</Text></View>
            <Text style={styles.status}>{statusOptions.find((s) => s.value === item.status)?.label || "Mới"}</Text>
          </View>
          <Text style={styles.title}>{item.title}</Text>
          <Text style={styles.customer}>{item.customer_label || "Khách hàng"} · {item.device_model || "Chưa rõ thiết bị"}</Text>
          {item.address ? <View style={styles.infoRow}><Ionicons name="location-outline" size={15} color="#D3DDF0" /><Text style={styles.infoText}>{item.address}</Text></View> : null}
        </View>

        <View style={styles.sectionCard}>
          <Text style={styles.sectionTitle}>Triệu chứng & bàn giao</Text>
          <Text style={styles.bodyText}>{item.symptom}</Text>
          {item.serial_number ? <Text style={styles.meta}>Serial: {item.serial_number}</Text> : null}
        </View>

        <View style={styles.actionRow}>
          <Action icon="sparkles-outline" label="Hỏi trợ lý" onPress={() => { setAnswer(null); setModal("assistant"); }} />
          <Action
            icon="add-circle-outline"
            label="Thêm checklist"
            onPress={() => offline
              ? Alert.alert("Cần kết nối mạng", "Có thể tick checklist hiện có và ghi nhận offline; thêm bước mới cần online.")
              : setModal("step")}
          />
          <Action icon="create-outline" label="Ghi nhận" onPress={() => setModal("note")} />
        </View>

        <View style={styles.sectionHeader}>
          <View>
            <Text style={styles.sectionTitle}>Checklist hiện trường</Text>
            <Text style={styles.meta}>{completedSteps}/{item.steps.length} bước hoàn tất</Text>
          </View>
          {!isResolved && (
            <TouchableOpacity
              onPress={() => offline
                ? Alert.alert("Cần kết nối mạng", "Thêm bước checklist mới cần online.")
                : setModal("step")}
            >
              <Ionicons name="add-circle" size={25} color={colors.primary} />
            </TouchableOpacity>
          )}
        </View>
        {item.steps.length ? item.steps.map((step, index) => (
          <TouchableOpacity
            key={step.id}
            style={styles.stepCard}
            onPress={() => toggleStep(step)}
            disabled={isResolved}
          >
            <View style={[styles.stepNumber, step.status === "done" && styles.stepDone]}>
              {step.status === "done" ? <Ionicons name="checkmark" size={15} color="#fff" /> : <Text style={styles.stepNumberText}>{index + 1}</Text>}
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.stepTitle, step.status === "done" && styles.strike]}>{step.title}</Text>
              {step.instruction ? <Text style={styles.stepInstruction}>{step.instruction}</Text> : null}
              {step.source ? <Text style={styles.source}>Nguồn: {step.source}</Text> : null}
              {step.result_note ? <Text style={styles.result}>{step.result_note}</Text> : null}
            </View>
          </TouchableOpacity>
        )) : (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyText}>Chưa có bước xử lý. Tạo checklist thủ công hoặc hỏi trợ lý dựa trên tài liệu đã nạp.</Text>
          </View>
        )}

        <Text style={[styles.sectionTitle, { marginTop: 18, marginBottom: 10 }]}>Nhật ký xử lý</Text>
        {item.events.slice().reverse().map((event) => (
          <View key={event.id} style={styles.eventRow}>
            <View style={styles.eventDot} />
            <View style={{ flex: 1 }}>
              <Text style={styles.eventText}>{event.detail}</Text>
              <Text style={styles.eventTime}>{new Date(event.created_at).toLocaleString("vi-VN")}</Text>
            </View>
          </View>
        ))}

        {!isResolved && (
          <View style={styles.statusActions}>
            {statusOptions.map((status) => (
              <TouchableOpacity
                key={status.value}
                disabled={busy || item.status === status.value}
                style={[styles.statusButton, status.value === "resolved" && styles.resolveButton]}
                onPress={() => changeStatus(status.value)}
              >
                <Text style={[styles.statusButtonText, status.value === "resolved" && { color: "#fff" }]}>{status.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}
      </ScrollView>

      <Modal visible={modal !== null} transparent animationType="slide" onRequestClose={() => setModal(null)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modal}>
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>
                {modal === "step" ? "Checklist mới" : modal === "note" ? "Ghi nhận hiện trường" : "Trợ lý kỹ thuật"}
              </Text>
              <TouchableOpacity onPress={() => setModal(null)}><Ionicons name="close" size={22} color={colors.textMuted} /></TouchableOpacity>
            </View>
            {modal === "step" ? (
              <>
                <TextInput style={styles.input} placeholder="Tên bước cần thực hiện" value={stepTitle} onChangeText={setStepTitle} placeholderTextColor={colors.textMuted} />
                <TextInput style={[styles.input, styles.multiline]} placeholder="Hướng dẫn ngắn" value={stepInstruction} onChangeText={setStepInstruction} multiline placeholderTextColor={colors.textMuted} />
                <TextInput style={styles.input} placeholder="Nguồn / trang SOP (nếu có)" value={stepSource} onChangeText={setStepSource} placeholderTextColor={colors.textMuted} />
                <GradientButton title="Thêm vào checklist" onPress={saveStep} loading={busy} />
              </>
            ) : modal === "note" ? (
              <>
                <TextInput style={[styles.input, styles.multiline, { minHeight: 130 }]} placeholder="Kết quả đo, trạng thái đèn, thao tác đã thử..." value={note} onChangeText={setNote} multiline placeholderTextColor={colors.textMuted} />
                <GradientButton title="Lưu ghi nhận" onPress={saveNote} loading={busy} />
              </>
            ) : (
              <>
                <Text style={styles.assistantHint}>Câu trả lời dựa trên tài liệu của tài khoản đang đăng nhập. Luôn kiểm tra hướng dẫn trước khi thao tác thiết bị.</Text>
                <TextInput style={[styles.input, styles.multiline]} placeholder="Ví dụ: Đèn PON nhấp nháy đỏ, nên kiểm tra gì trước?" value={question} onChangeText={setQuestion} multiline placeholderTextColor={colors.textMuted} />
                <GradientButton title="Tìm trong tri thức" onPress={askAssistant} loading={busy} />
                {answer ? (
                  <ScrollView style={styles.answerCard}>
                    <Text style={styles.answerText}>{markdownToPlain(answer.answer)}</Text>
                    {answer.sources.length > 0 ? <Text style={styles.source}>Nguồn: {answer.sources.join(", ")}</Text> : null}
                  </ScrollView>
                ) : null}
              </>
            )}
          </View>
        </View>
      </Modal>
    </View>
  );
}

function describeAction(action: PendingFieldAction): string {
  if (action.kind === "status") {
    const label = statusOptions.find((s) => s.value === action.status)?.label ?? action.status;
    return `Chuyển trạng thái: ${label}`;
  }
  if (action.kind === "step_update") {
    return `Checklist #${action.step_id}: ${action.status === "done" ? "hoàn tất" : "bỏ đánh dấu"}`;
  }
  return `Ghi nhận: ${action.detail}`;
}

function Action({ icon, label, onPress }: { icon: keyof typeof Ionicons.glyphMap; label: string; onPress: () => void }) {
  return (
    <TouchableOpacity style={styles.action} onPress={onPress}>
      <Ionicons name={icon} size={21} color={colors.primary} />
      <Text style={styles.actionText}>{label}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: colors.background },
  loadError: { color: colors.destructive, textAlign: "center", paddingHorizontal: 24, lineHeight: 21 },
  retryButton: { marginTop: 14, paddingHorizontal: 18, paddingVertical: 10, borderRadius: 10, backgroundColor: colors.primary },
  retryText: { color: "#fff", fontWeight: "700" },
  pendingWarning: { color: colors.warning, textAlign: "center", paddingHorizontal: 24, marginTop: 12, lineHeight: 18 },
  content: { padding: 16, paddingBottom: 34 },
  syncBanner: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "#EEF2FF", borderRadius: 12, padding: 12, marginBottom: 12 },
  offlineBanner: { backgroundColor: "#FFF7ED" },
  syncTitle: { color: colors.text, fontSize: 12, fontWeight: "800" },
  syncHint: { color: colors.textMuted, fontSize: 10, lineHeight: 15, marginTop: 3 },
  rejectedCard: { backgroundColor: "#FEF2F2", borderRadius: 12, padding: 12, marginBottom: 12 },
  rejectedWrap: { alignSelf: "stretch", paddingHorizontal: 16, marginTop: 12 },
  rejectedTitle: { color: colors.destructive, fontSize: 12, fontWeight: "800" },
  rejectedRow: { borderTopWidth: 1, borderTopColor: "#FECACA", paddingTop: 9, marginTop: 9 },
  rejectedError: { color: colors.destructive, fontSize: 10, marginTop: 3 },
  rejectedButtons: { flexDirection: "row", gap: 18, marginTop: 7 },
  rejectedRetry: { color: colors.primary, fontSize: 12, fontWeight: "700" },
  rejectedDiscard: { color: colors.destructive, fontSize: 12, fontWeight: "700" },
  hero: { backgroundColor: "#16243A", borderRadius: 18, padding: 18, marginBottom: 12 },
  heroTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  caseId: { backgroundColor: "#33445F", borderRadius: 20, paddingHorizontal: 9, paddingVertical: 5 },
  caseIdText: { color: "#E6ECF5", fontWeight: "800", fontSize: 10 },
  status: { color: "#C5D0E4", fontSize: 11, fontWeight: "700" },
  title: { color: "#fff", fontSize: 22, fontWeight: "800", lineHeight: 28, marginTop: 16 },
  customer: { color: "#C2CDE0", fontSize: 12, marginTop: 7 },
  infoRow: { flexDirection: "row", gap: 6, alignItems: "center", marginTop: 10 },
  infoText: { color: "#D3DDF0", fontSize: 11, flex: 1 },
  sectionCard: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 15, marginBottom: 11 },
  sectionTitle: { color: colors.text, fontWeight: "800", fontSize: 15 },
  bodyText: { color: colors.text, fontSize: 13, lineHeight: 20, marginTop: 8 },
  meta: { color: colors.textMuted, fontSize: 11, marginTop: 6 },
  actionRow: { flexDirection: "row", gap: 8, marginBottom: 19 },
  action: { flex: 1, alignItems: "center", justifyContent: "center", minHeight: 68, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md },
  actionText: { color: colors.text, fontWeight: "700", fontSize: 10, marginTop: 6 },
  sectionHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 9 },
  stepCard: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: 13, flexDirection: "row", gap: 11, marginBottom: 8 },
  stepNumber: { width: 26, height: 26, borderRadius: 13, backgroundColor: "#EEF2FF", alignItems: "center", justifyContent: "center" },
  stepDone: { backgroundColor: colors.success },
  stepNumberText: { color: colors.primary, fontSize: 11, fontWeight: "800" },
  stepTitle: { color: colors.text, fontSize: 13, fontWeight: "700", lineHeight: 18 },
  strike: { color: colors.textMuted, textDecorationLine: "line-through" },
  stepInstruction: { color: colors.textMuted, fontSize: 11, lineHeight: 17, marginTop: 5 },
  source: { color: colors.primary, fontSize: 10, marginTop: 6 },
  result: { color: colors.success, fontSize: 10, marginTop: 5 },
  emptyCard: { padding: 15, backgroundColor: colors.card, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  emptyText: { color: colors.textMuted, fontSize: 12, lineHeight: 18 },
  eventRow: { flexDirection: "row", gap: 10, paddingVertical: 8 },
  eventDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.primary, marginTop: 4 },
  eventText: { color: colors.text, fontSize: 12, lineHeight: 17 },
  eventTime: { color: colors.textMuted, fontSize: 10, marginTop: 4 },
  statusActions: { gap: 8, marginTop: 18 },
  statusButton: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 11, paddingVertical: 11, alignItems: "center" },
  statusButtonText: { color: colors.text, fontSize: 12, fontWeight: "700" },
  resolveButton: { backgroundColor: colors.success, borderColor: colors.success },
  modalBackdrop: { flex: 1, backgroundColor: "#11182799", justifyContent: "flex-end" },
  modal: { backgroundColor: colors.background, borderTopLeftRadius: 22, borderTopRightRadius: 22, padding: 18, paddingBottom: 28, maxHeight: "90%" },
  modalHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 13 },
  modalTitle: { color: colors.text, fontSize: 18, fontWeight: "800" },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: 11, paddingHorizontal: 12, paddingVertical: 11, backgroundColor: colors.card, color: colors.text, fontSize: 13, marginBottom: 9 },
  multiline: { minHeight: 76, textAlignVertical: "top" },
  assistantHint: { color: colors.textMuted, fontSize: 11, lineHeight: 16, marginBottom: 10 },
  answerCard: { maxHeight: 280, backgroundColor: colors.card, borderRadius: 12, padding: 13, marginTop: 12, borderWidth: 1, borderColor: colors.border },
  answerText: { color: colors.text, fontSize: 12, lineHeight: 19 },
});
