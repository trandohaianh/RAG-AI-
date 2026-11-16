import { ActivityIndicator, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "../lib/auth";
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
import FieldTeamScreen from "../screens/FieldTeamScreen";

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

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    );
  }

  return (
    <NavigationContainer>
      {user ? (
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
        </AppStack.Navigator>
      ) : <AuthNavigator />}
    </NavigationContainer>
  );
}
