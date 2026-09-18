import { Linking, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useAuth } from '../../src/auth/AuthContext';
import { palette, radius } from '../../src/theme';

type MenuRowProps = {
  icon: string;
  label: string;
  detail?: string;
  external?: boolean;
  onPress: () => void;
};

function MenuRow({ icon, label, detail, external, onPress }: MenuRowProps) {
  return (
    <Pressable style={({ pressed }) => [s.row, pressed && s.pressed]} onPress={onPress}>
      <View style={s.rowIcon}><Text style={s.rowIconText}>{icon}</Text></View>
      <View style={s.rowCopy}><Text style={s.rowText}>{label}</Text>{detail && <Text style={s.rowDetail}>{detail}</Text>}</View>
      <Text style={s.arrow}>{external ? '↗' : '›'}</Text>
    </Pressable>
  );
}

export default function Profile() {
  const { user, logout } = useAuth();
  const creator = user?.role === 'creator';
  const displayName = user?.stage_name || user?.username || 'BeatHub member';
  const initial = displayName.trim().charAt(0).toUpperCase() || 'B';
  const open = (path: string) => Linking.openURL(`https://mybeathub.com${path}`);

  return (
    <SafeAreaView style={s.safe}>
      <ScrollView contentContainerStyle={s.container} showsVerticalScrollIndicator={false}>
        <Text style={s.kicker}>YOUR BEATHUB</Text>
        <Text style={s.title}>Profile</Text>

        <View style={s.profileCard}>
          <View style={s.profileGlow} />
          <View style={s.avatar}><Text style={s.avatarText}>{initial}</Text></View>
          <View style={s.identity}>
            <Text style={s.name}>{displayName}</Text>
            <Text style={s.email}>{user?.email}</Text>
            <View style={s.rolePill}><View style={s.roleDot} /><Text style={s.role}>{creator ? 'CREATOR ACCOUNT' : 'ARTIST ACCOUNT'}</Text></View>
          </View>
        </View>

        {creator && (
          <>
            <Text style={s.section}>CREATOR WORKSPACE</Text>
            <View style={s.menu}>
              <MenuRow icon="♫" label="Creator Studio" detail="Performance, catalogue and quick actions" onPress={() => router.push('/creator-dashboard')} />
              <MenuRow icon="◎" label="Public profile" detail="Your identity and shareable store" onPress={() => router.push('/creator-profile')} />
              <MenuRow icon="↑" label="Upload Beat / Track" detail="Release new music to the marketplace" onPress={() => router.push('/creator-upload')} />
              <MenuRow icon="₭" label="Sales & withdrawals" detail="Earnings, history and M-Pesa payout" onPress={() => router.push('/creator-finances')} />
            </View>
          </>
        )}

        <Text style={s.section}>ACCOUNT</Text>
        <View style={s.menu}>
          <MenuRow icon="●" label="Notifications" detail="Activity and important updates" onPress={() => router.push('/notifications')} />
          <MenuRow icon="⌁" label="Change password" external onPress={() => open('/forgot-password')} />
        </View>

        <Text style={s.section}>BEATHUB</Text>
        <View style={s.menu}>
          <MenuRow icon="?" label="Help & support" external onPress={() => open('/support')} />
          <MenuRow icon="§" label="Terms" external onPress={() => open('/terms')} />
          <MenuRow icon="◈" label="Privacy" external onPress={() => open('/privacy')} />
        </View>

        <Pressable
          style={({ pressed }) => [s.logout, pressed && s.pressed]}
          onPress={async () => { await logout(); router.replace('/(auth)/login'); }}
        >
          <Text style={s.logoutText}>SIGN OUT OF BEATHUB</Text>
        </Pressable>
        <Text style={s.version}>BEATHUB MOBILE · VERSION 1.0.0</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.canvas },
  container: { paddingHorizontal: 18, paddingTop: 18, paddingBottom: 45 },
  kicker: { color: palette.gold, fontSize: 9, fontWeight: '900', letterSpacing: 2.2 },
  title: { color: palette.white, fontSize: 34, fontWeight: '900', letterSpacing: -1.3, marginTop: 4, marginBottom: 18 },
  profileCard: { overflow: 'hidden', flexDirection: 'row', alignItems: 'center', gap: 15, borderWidth: 1, borderColor: palette.borderGold, borderRadius: radius.lg, backgroundColor: '#18121F', padding: 19 },
  profileGlow: { position: 'absolute', width: 150, height: 150, borderRadius: 75, backgroundColor: 'rgba(255,184,0,0.08)', right: -45, top: -65 },
  avatar: { width: 63, height: 63, borderRadius: 21, backgroundColor: palette.gold, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: palette.ink, fontSize: 29, fontWeight: '900' },
  identity: { flex: 1 },
  name: { color: palette.white, fontSize: 20, fontWeight: '900' },
  email: { color: palette.muted, fontSize: 12, marginTop: 4 },
  rolePill: { alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.pill, backgroundColor: 'rgba(24,213,170,0.1)', borderWidth: 1, borderColor: 'rgba(24,213,170,0.22)', paddingHorizontal: 9, paddingVertical: 5, marginTop: 10 },
  roleDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: palette.teal },
  role: { color: palette.teal, fontSize: 8, fontWeight: '900', letterSpacing: 0.8 },
  section: { color: palette.muted2, fontSize: 9, fontWeight: '900', letterSpacing: 1.7, marginTop: 27, marginBottom: 9, paddingLeft: 3 },
  menu: { overflow: 'hidden', borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: palette.border },
  pressed: { opacity: 0.76 },
  rowIcon: { width: 38, height: 38, borderRadius: 12, backgroundColor: 'rgba(255,184,0,0.09)', alignItems: 'center', justifyContent: 'center' },
  rowIconText: { color: palette.gold, fontSize: 17, fontWeight: '900' },
  rowCopy: { flex: 1 },
  rowText: { color: palette.white, fontSize: 14, fontWeight: '800' },
  rowDetail: { color: palette.muted2, fontSize: 10, marginTop: 3 },
  arrow: { color: palette.muted2, fontSize: 22 },
  logout: { marginTop: 28, height: 51, borderRadius: radius.sm, borderWidth: 1, borderColor: 'rgba(255,143,156,0.32)', backgroundColor: 'rgba(255,143,156,0.06)', alignItems: 'center', justifyContent: 'center' },
  logoutText: { color: '#FFB0BA', fontSize: 10, fontWeight: '900', letterSpacing: 1 },
  version: { color: '#554E60', textAlign: 'center', fontSize: 9, fontWeight: '800', letterSpacing: 0.8, marginTop: 17 },
});
