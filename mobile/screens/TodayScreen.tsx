import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Platform,
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
import { directionsUrl, loadMySchedule, timeOf, type ScheduleResult } from "../lib/mySchedule";
import { enablePush, getPushState, type PushState } from "../lib/push";
import { colors, radius } from "../lib/theme";

const PRIORITY: Record<api.MyScheduleStop["priority"], { label: string; color: string } | null> = {
  urgent: { label: "Khẩn", color: colors.destructive },
  high: { label: "Cao", color: colors.warning },
  normal: null,
  low: null,
};

const STATUS_LABEL: Record<string, string> = {
  open: "Mới",
  in_progress: "Đang xử lý",
  waiting: "Chờ khách",
  escalated: "Chuyển cấp",
  resolved: "Đã xong",
};

/** M11 — lịch hôm nay / ngày mai của kỹ thuật viên (docs/design/M11_lich_hom_nay_mobile.md). */
export default function TodayScreen({ navigation, route }: any) {
  const [dayOffset, setDayOffset] = useState<0 | 1>(0);
  // Mở từ khối "Lịch hôm nay" (Tổng quan) kèm `day`: luôn về đúng ngày đó, kể cả khi lần trước
  // người dùng để tab ở "Ngày mai".
  const requestedDay = route?.params?.day;
  useFocusEffect(
    useCallback(() => {
      if (requestedDay === 0 || requestedDay === 1) {
        setDayOffset(requestedDay);
        navigation.setParams({ day: undefined });
      }
    }, [requestedDay, navigation])
  );
  const [result, setResult] = useState<ScheduleResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (offset: 0 | 1) => {
    setResult(await loadMySchedule(api.localDate(offset)));
    setLoading(false);
    setRefreshing(false);
  }, []);

  useFocusEffect(useCallback(() => { load(dayOffset); }, [load, dayOffset]));

  // M15: thẻ "Bật thông báo" (chỉ Android, máy thật, máy chủ đã bật thông báo).
  const [push, setPush] = useState<PushState>("unsupported");
  const [enabling, setEnabling] = useState(false);
  useFocusEffect(
    useCallback(() => {
      let alive = true;
      getPushState().then((state) => { if (alive) setPush(state); }).catch(() => undefined);
      return () => { alive = false; };
    }, [])
  );
  // Đang mở app mà có thông báo mới -> tự làm mới lịch.
  useEffect(() => {
    if (Platform.OS !== "android" || push !== "on") return;
    let remove: (() => void) | undefined;
    void import("expo-notifications").then((Notifications) => {
      const sub = Notifications.addNotificationReceivedListener(() => { load(dayOffset); });
      remove = () => sub.remove();
    });
    return () => remove?.();
  }, [push, load, dayOffset]);
  const turnOn = async () => {
    setEnabling(true);
    try {
      setPush(await enablePush());
    } catch {
      setPush("off");
    } finally {
      setEnabling(false);
    }
  };

  const openCase = (caseId: number) =>
    navigation.navigate("Cases", { screen: "CaseDetail", params: { caseId } });

  const schedule = result?.schedule;
  return (
    <ScrollView
      style={styles.flex}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(dayOffset); }} />
      }
    >
      <Text style={styles.title}>Lịch của tôi</Text>
      <View style={styles.switcher}>
        {([0, 1] as const).map((offset) => (
          <TouchableOpacity
            key={offset}
            style={[styles.switchButton, dayOffset === offset && styles.switchActive]}
            onPress={() => { setLoading(true); setDayOffset(offset); }}
            accessibilityRole="button"
            accessibilityState={{ selected: dayOffset === offset }}
          >
            <Text style={[styles.switchText, dayOffset === offset && styles.switchTextActive]}>
              {offset === 0 ? "Hôm nay" : "Ngày mai"} · {formatDay(api.localDate(offset))}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {result?.offline && result.cachedAt ? (
        <Text style={styles.offline}>
          Đang offline — lịch cập nhật lúc {timeOf(result.cachedAt)} {formatDay(result.cachedAt.slice(0, 10))}
        </Text>
      ) : null}
      {result?.error ? <Text style={styles.error}>{result.error}</Text> : null}

      {push === "off" ? (
        <View style={styles.pushCard}>
          <Ionicons name="notifications-outline" size={22} color={colors.primary} />
          <View style={{ flex: 1 }}>
            <Text style={styles.pushTitle}>Bật thông báo</Text>
            <Text style={styles.pushHint}>Biết ngay khi trưởng đội giao ca mới hoặc đổi lịch của bạn.</Text>
          </View>
          <TouchableOpacity style={styles.pushButton} onPress={turnOn} disabled={enabling} accessibilityRole="button">
            {enabling ? <ActivityIndicator color="#fff" size="small" /> : <Text style={styles.pushButtonText}>Bật</Text>}
          </TouchableOpacity>
        </View>
      ) : push === "denied" ? (
        <TouchableOpacity style={styles.pushDenied} onPress={() => Linking.openSettings()} accessibilityRole="button">
          <Text style={styles.pushHint}>
            Thông báo đang tắt cho FieldOps. Bấm để mở Cài đặt của điện thoại nếu muốn bật lại.
          </Text>
        </TouchableOpacity>
      ) : null}

      {loading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 24 }} />
      ) : schedule ? (
        <>
          <View style={styles.progressCard}>
            <Text style={styles.progressValue}>
              {schedule.done}/{schedule.total}
            </Text>
            <Text style={styles.progressLabel}>ca đã xong {dayOffset === 0 ? "hôm nay" : "ngày mai"}</Text>
          </View>

          {schedule.stops.length ? (
            schedule.stops.map((stop) => <StopRow key={stop.case_id} stop={stop} onOpen={openCase} />)
          ) : (
            <View style={styles.emptyCard}>
              <Ionicons name="calendar-outline" size={26} color={colors.textMuted} />
              <Text style={styles.emptyTitle}>Chưa có ca nào được xếp lịch</Text>
              <Text style={styles.emptyHint}>Trưởng đội xếp lịch trên web thì ca sẽ hiện ở đây.</Text>
            </View>
          )}

          {schedule.unscheduled.length ? (
            <>
              <Text style={styles.sectionTitle}>Được giao, chưa xếp lịch</Text>
              {schedule.unscheduled.map((stop) => (
                <StopRow key={stop.case_id} stop={stop} onOpen={openCase} unscheduled />
              ))}
            </>
          ) : null}
        </>
      ) : null}
    </ScrollView>
  );
}

function StopRow({
  stop,
  onOpen,
  unscheduled = false,
}: {
  stop: api.MyScheduleStop;
  onOpen: (caseId: number) => void;
  unscheduled?: boolean;
}) {
  const done = stop.status === "resolved";
  const priority = PRIORITY[stop.priority];
  const url = directionsUrl(stop);
  return (
    <TouchableOpacity style={[styles.stop, done && styles.stopDone]} onPress={() => onOpen(stop.case_id)}>
      <View style={styles.timeCol}>
        <Text style={styles.time}>{unscheduled ? "—" : timeOf(stop.scheduled_start)}</Text>
        {stop.route_order ? <Text style={styles.order}>#{stop.route_order}</Text> : null}
      </View>
      <View style={{ flex: 1 }}>
        <View style={styles.row}>
          <Text style={[styles.stopTitle, done && styles.strike]} numberOfLines={1}>
            {stop.title}
          </Text>
          {priority ? (
            <Text style={[styles.badge, { color: priority.color, borderColor: priority.color }]}>{priority.label}</Text>
          ) : null}
        </View>
        <Text style={styles.sub} numberOfLines={2}>
          {stop.address || "Chưa có địa chỉ"}
          {stop.customer_label ? ` · ${stop.customer_label}` : ""}
        </Text>
        <Text style={styles.meta}>
          {STATUS_LABEL[stop.status] ?? stop.status}
          {stop.device_model ? ` · ${stop.device_model}` : ""} · {stop.workspace}
        </Text>
      </View>
      {url && !done ? (
        <TouchableOpacity
          style={styles.navButton}
          onPress={() => Linking.openURL(url)}
          accessibilityLabel="Chỉ đường"
        >
          <Ionicons name="navigate" size={18} color={colors.primary} />
        </TouchableOpacity>
      ) : null}
    </TouchableOpacity>
  );
}

function formatDay(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${day}/${month}`;
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, paddingTop: 56, paddingBottom: 32 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800", marginBottom: 12 },
  switcher: { flexDirection: "row", gap: 8, marginBottom: 12 },
  switchButton: { flex: 1, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center" },
  switchActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  switchText: { color: colors.text, fontWeight: "700", fontSize: 13 },
  switchTextActive: { color: "#fff" },
  offline: { backgroundColor: colors.warningSoft, color: colors.warningText, borderRadius: 10, padding: 12, marginBottom: 12 },
  error: { backgroundColor: colors.dangerSoft, color: colors.destructive, borderRadius: 10, padding: 12, marginBottom: 12 },
  progressCard: { flexDirection: "row", alignItems: "baseline", gap: 8, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 14, marginBottom: 12 },
  progressValue: { color: colors.text, fontSize: 24, fontWeight: "800" },
  progressLabel: { color: colors.textMuted, fontSize: 13 },
  stop: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 13, marginBottom: 8 },
  stopDone: { opacity: 0.55 },
  timeCol: { width: 52, alignItems: "center" },
  time: { color: colors.text, fontWeight: "800", fontSize: 15 },
  order: { color: colors.textMuted, fontSize: 11, marginTop: 2 },
  row: { flexDirection: "row", alignItems: "center", gap: 6 },
  stopTitle: { color: colors.text, fontWeight: "700", fontSize: 14, flexShrink: 1 },
  strike: { textDecorationLine: "line-through" },
  badge: { fontSize: 10, fontWeight: "800", borderWidth: 1, borderRadius: 8, paddingHorizontal: 6, paddingVertical: 1 },
  sub: { color: colors.text, fontSize: 12, marginTop: 4 },
  meta: { color: colors.textMuted, fontSize: 11, marginTop: 3 },
  navButton: { width: 40, height: 40, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.primarySoft },
  sectionTitle: { color: colors.text, fontSize: 16, fontWeight: "800", marginTop: 16, marginBottom: 10 },
  emptyCard: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 20, alignItems: "center" },
  emptyTitle: { color: colors.text, fontSize: 14, fontWeight: "700", marginTop: 8 },
  emptyHint: { color: colors.textMuted, textAlign: "center", fontSize: 12, marginTop: 5 },
  pushCard: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.primarySoft, borderRadius: radius.md, padding: 14, marginBottom: 12 },
  pushTitle: { color: colors.text, fontWeight: "800", fontSize: 14 },
  pushHint: { color: colors.textMuted, fontSize: 12, marginTop: 2 },
  pushButton: { backgroundColor: colors.primary, borderRadius: 10, paddingHorizontal: 16, paddingVertical: 9, minWidth: 56, alignItems: "center" },
  pushButtonText: { color: "#fff", fontWeight: "800" },
  pushDenied: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 12, marginBottom: 12 },
});
