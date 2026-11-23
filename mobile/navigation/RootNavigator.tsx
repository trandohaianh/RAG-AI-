import React, { useEffect, useState } from "react";
import { ActivityIndicator, Platform, View } from "react-native";
import { NavigationContainer, createNavigationContainerRef } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "../lib/auth";
import * as api from "../lib/api";
import { parsePushTarget } from "../lib/pushTarget";
import type { NotificationResponse } from "expo-notifications";
import { colors } from "../lib/theme";

import LoginScreen from "../screens/LoginScreen";
import RegisterScreen from "../screens/RegisterScreen";
import ChatListScreen from "../screens/ChatListScreen";
import ChatScreen from "../screens/ChatScreen";
import DocumentsScreen from "../screens/DocumentsScreen";
import SettingsScreen from "../screens/SettingsScreen";
import FieldHomeScreen from "../screens/FieldHomeScreen";
import FieldCasesScreen from "../screens/FieldCasesScreen";
import FieldCaseDetailScreen from "../screens/FieldCaseDetailScreen";
import LocationPickerScreen from "../screens/LocationPickerScreen";
import FieldTeamScreen from "../screens/FieldTeamScreen";
import TodayScreen from "../screens/TodayScreen";
import PrivacyScreen from "../screens/PrivacyScreen";
import PrivacyConsentScreen from "../screens/PrivacyConsentScreen";
import { needsConsent } from "../lib/privacy";

const navigationRef = createNavigationContainerRef<any>();
let handledNotification: string | null = null;

/** M15: bấm thông báo đẩy -> mở tab Hôm nay đúng ngày (cả khi app đang tắt hẳn). */
function usePushNavigation(signedIn: boolean, ready: boolean) {
  useEffect(() => {
    if (!signedIn || !ready || Platform.OS !== "android") return;
    let remove: (() => void) | undefined;
    let alive = true;
    void import("expo-notifications").then((Notifications) => {
      if (!alive) return;
      const open = (response: NotificationResponse | null) => {
        if (!response || response.notification.request.identifier === handledNotification) return;
        handledNotification = response.notification.request.identifier;
        const target = parsePushTarget(response.notification.request.content.data, api.localDate(0), api.localDate(1));
        if (target && navigationRef.isReady()) {
          navigationRef.navigate("Main", { screen: "Today", params: { day: target.day } });
        }
      };
      Notifications.getLastNotificationResponseAsync().then(open).catch(() => undefined);
      const sub = Notifications.addNotificationResponseReceivedListener(open);
      remove = () => sub.remove();
    });
    return () => { alive = false; remove?.(); };
  }, [signedIn, ready]);
}

const AuthStack = createNativeStackNavigator();
const AppStack = createNativeStackNavigator();
const CasesStack = createNativeStackNavigator();
const Tabs = createBottomTabNavigator();

const headerOptions = {
  headerStyle: { backgroundColor: colors.card },
  headerTitleStyle: { color: colors.text, fontWeight: "700" as const },
  headerTintColor: colors.primary,
};

function AuthNavigator() {
  return (
    <AuthStack.Navigator screenOptions={{ headerShown: false }}>
      <AuthStack.Screen name="Login" component={LoginScreen} />
      <AuthStack.Screen name="Register" component={RegisterScreen} />
      <AuthStack.Screen
        name="Privacy"
        component={PrivacyScreen}
        options={{ ...headerOptions, headerShown: true, title: "Chính sách quyền riêng tư" }}
      />
    </AuthStack.Navigator>
  );
}

function CasesNavigator() {
  return (
    <CasesStack.Navigator screenOptions={headerOptions}>
      <CasesStack.Screen
        name="CaseList"
        component={FieldCasesScreen}
        options={{ title: "Ca sửa chữa", headerShown: false }}
      />
      <CasesStack.Screen
        name="CaseDetail"
        component={FieldCaseDetailScreen}
        options={{ title: "Chi tiết ca sửa" }}
      />
      <CasesStack.Screen
        name="LocationPicker"
        component={LocationPickerScreen}
        options={{ title: "Vị trí khách" }}
      />
    </CasesStack.Navigator>
  );
}

function MainTabs() {
  return (
    <Tabs.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarIcon: ({ color, size }) => {
          const icons: Record<string, keyof typeof Ionicons.glyphMap> = {
            Today: "today",
            Overview: "grid",
            Cases: "construct",
            Knowledge: "book",
            Team: "people",
            Settings: "settings",
          };
          return <Ionicons name={icons[route.name]} size={size} color={color} />;
        },
      })}
    >
      {/* M11: tab đầu tiên — kỹ thuật viên mở app là thấy lịch trong ngày. */}
      <Tabs.Screen
        name="Today"
        component={TodayScreen}
        options={{ title: "Hôm nay" }}
      />
      <Tabs.Screen
        name="Overview"
        component={FieldHomeScreen}
        options={{ title: "Tổng quan" }}
      />
      <Tabs.Screen
        name="Cases"
        component={CasesNavigator}
        options={{ title: "Ca sửa" }}
      />
      <Tabs.Screen
        name="Knowledge"
        component={DocumentsScreen}
        options={{ title: "Tri thức", ...headerOptions, headerShown: true }}
      />
      <Tabs.Screen
        name="Team"
        component={FieldTeamScreen}
        options={{ title: "Đội", ...headerOptions, headerShown: true }}
      />
      <Tabs.Screen
        name="Settings"
        component={SettingsScreen}
        options={{ title: "Cài đặt", ...headerOptions, headerShown: true }}
      />
    </Tabs.Navigator>
  );
}

export default function RootNavigator() {
  const { user, loading } = useAuth();
  const [ready, setReady] = useState(false);
  usePushNavigation(!!user, ready);

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    );
  }

  return (
    <NavigationContainer ref={navigationRef} onReady={() => setReady(true)}>
      {user && needsConsent(user) ? (
        <PrivacyConsentScreen />
      ) : user ? (
        <AppStack.Navigator screenOptions={headerOptions}>
          <AppStack.Screen
            name="Main"
            component={MainTabs}
            options={{ headerShown: false }}
          />
          <AppStack.Screen
            name="ChatList"
            component={ChatListScreen}
            options={{ title: "Trợ lý tri thức" }}
          />
          <AppStack.Screen
            name="ChatDetail"
            component={ChatScreen}
            options={{ title: "Trợ lý tri thức" }}
          />
          <AppStack.Screen
            name="Privacy"
            component={PrivacyScreen}
            options={{ title: "Chính sách quyền riêng tư" }}
          />
        </AppStack.Navigator>
      ) : <AuthNavigator />}
    </NavigationContainer>
  );
}
