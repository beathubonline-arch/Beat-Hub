import { Tabs } from 'expo-router';

export default function TabsLayout() {
  return <Tabs screenOptions={{ headerShown: false, tabBarStyle: { backgroundColor: '#131019', borderTopColor: '#24202c' }, tabBarActiveTintColor: '#ffb800', tabBarInactiveTintColor: '#777180' }}>
    <Tabs.Screen name="home" options={{ title: 'Home' }} />
    <Tabs.Screen name="beats" options={{ title: 'Pulse' }} />
    <Tabs.Screen name="library" options={{ title: 'Library' }} />
    <Tabs.Screen name="profile" options={{ title: 'Profile' }} />
  </Tabs>;
}

