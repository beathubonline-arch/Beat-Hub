import { Tabs } from 'expo-router';
import { StyleSheet, Text } from 'react-native';
import { palette } from '../../src/theme';

const icons: Record<string, string> = {
  home: '◆',
  beats: '♪',
  library: '▣',
  profile: '●',
};

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarHideOnKeyboard: true,
        tabBarStyle: s.tabBar,
        tabBarItemStyle: s.tabItem,
        tabBarLabelStyle: s.label,
        tabBarActiveTintColor: palette.gold,
        tabBarInactiveTintColor: palette.muted2,
        tabBarIcon: ({ color, focused }) => (
          <Text style={[s.icon, { color }, focused && s.activeIcon]}>{icons[route.name] || '•'}</Text>
        ),
      })}
    >
      <Tabs.Screen name="home" options={{ title: 'Home' }} />
      <Tabs.Screen name="beats" options={{ title: 'Pulse' }} />
      <Tabs.Screen name="library" options={{ title: 'Library' }} />
      <Tabs.Screen name="profile" options={{ title: 'Profile' }} />
    </Tabs>
  );
}

const s = StyleSheet.create({
  tabBar: {
    height: 72,
    paddingTop: 8,
    paddingBottom: 9,
    backgroundColor: '#100D16',
    borderTopWidth: 1,
    borderTopColor: palette.border,
    elevation: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -8 },
    shadowOpacity: 0.3,
    shadowRadius: 18,
  },
  tabItem: { borderRadius: 14 },
  label: { fontSize: 10, fontWeight: '800', letterSpacing: 0.2 },
  icon: { fontSize: 18, fontWeight: '900', lineHeight: 21 },
  activeIcon: { textShadowColor: 'rgba(255,184,0,0.45)', textShadowRadius: 10 },
});
