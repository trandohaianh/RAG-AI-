import { StatusBar } from "expo-status-bar";
import { scheme } from "./lib/theme";
import { AuthProvider } from "./lib/auth";
import RootNavigator from "./navigation/RootNavigator";

export default function App() {
  return (
    <AuthProvider>
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
      <RootNavigator />
    </AuthProvider>
  );
}
