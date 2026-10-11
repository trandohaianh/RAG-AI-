import React from "react";
import { StatusBar } from "expo-status-bar";
import { AuthProvider } from "./lib/auth";
import RootNavigator from "./navigation/RootNavigator";

export default function App() {
  return (
    <AuthProvider>
      <StatusBar style="dark" />
      <RootNavigator />
    </AuthProvider>
  );
}
