import { router } from 'expo-router';
import { Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useAuth } from '../../src/auth/AuthContext';
import { palette, radius, shadow } from '../../src/theme';

type ActionCardProps = {
  eyebrow: string;
  title: string;
  copy: string;
  icon: string;
  accent?: 'gold' | 'teal';
  onPress: () => void;
};

function ActionCard({ eyebrow, title, copy, icon, accent = 'gold', onPress }: ActionCardProps) {
  const color = accent === 'teal' ? palette.teal : palette.gold;
  return (
    <Pressable style={({ pressed }) => [s.actionCard, pressed && s.pressed]} onPress={onPress}>
      <View style={[s.iconBox, { backgroundColor: `${color}16`, borderColor: `${color}35` }]}>
        <Text style={[s.icon, { color }]}>{icon}</Text>
      </View>
      <View style={s.actionCopy}>
        <Text style={[s.actionEyebrow, { color }]}>{eyebrow}</Text>
        <Text style={s.actionTitle}>{title}</Text>
        <Text style={s.actionText}>{copy}</Text>
      </View>
      <Text style={s.arrow}>›</Text>
    </Pressable>
  );
}

export default function Home() {
  const { user } = useAuth();
  const creator = user?.role === 'creator';
  const displayName = user?.stage_name || user?.username || 'Creator';

  return (
    <SafeAreaView style={s.safe}>
      <ScrollView contentContainerStyle={s.container} showsVerticalScrollIndicator={false}>
        <View style={s.brandRow}>
          <View style={s.brandMark}><Text style={s.brandMarkText}>B</Text></View>
          <Text style={s.brand}>BEAT<Text style={s.brandGold}>HUB</Text></Text>
          <View style={s.livePill}><View style={s.liveDot} /><Text style={s.liveText}>LIVE</Text></View>
        </View>

        <View style={s.hero}>
          <View style={s.heroGlow} />
          <Text style={s.eyebrow}>THE HOME OF COOL BEATS</Text>
          <Text style={s.greeting}>Welcome back, {displayName}</Text>
          <Text style={s.heroTitle}>
            {creator ? 'SELL THE ' : 'FIND YOUR NEXT '}
            <Text style={s.gold}>{creator ? 'SOUND.' : 'SOUND.'}</Text>
          </Text>
          <Text style={s.heroTitle}>
            {creator ? 'KEEP YOUR ' : 'BUILD YOUR '}
            <Text style={s.teal}>{creator ? 'FLOW.' : 'MOMENT.'}</Text>
          </Text>
          <Text style={s.heroCopy}>
            {creator
              ? 'Your music, sales, audience and earnings—together in one studio built for the culture.'
              : 'Fresh African sound, independent creators and licences ready for your next release.'}
          </Text>

          <View style={s.waveform}>
            {[18, 34, 24, 52, 30, 62, 39, 72, 28, 54, 34, 66, 22, 48, 30, 58].map((height, index) => (
              <View key={index} style={[s.waveBar, { height, backgroundColor: index % 4 === 0 ? palette.teal : palette.gold }]} />
            ))}
          </View>

          <Pressable style={({ pressed }) => [s.primaryButton, pressed && s.pressed]} onPress={() => router.push('/(tabs)/beats')}>
            <Text style={s.primaryText}>EXPLORE BEATS</Text>
            <Text style={s.primaryArrow}>→</Text>
          </Pressable>
        </View>

        <View style={s.statRow}>
          <View style={s.stat}><Text style={s.statNumber}>01</Text><Text style={s.statLabel}>DISCOVER</Text></View>
          <View style={s.stat}><Text style={s.statNumber}>02</Text><Text style={s.statLabel}>CONNECT</Text></View>
          <View style={s.stat}><Text style={s.statNumber}>03</Text><Text style={s.statLabel}>CREATE</Text></View>
        </View>

        <View style={s.sectionHeader}>
          <View>
            <Text style={s.sectionKicker}>{creator ? 'YOUR WORKSPACE' : 'START CREATING'}</Text>
            <Text style={s.sectionTitle}>Everything within reach.</Text>
          </View>
        </View>

        {creator && (
          <ActionCard
            eyebrow="CREATOR STUDIO"
            title="Run your music business"
            copy="Manage releases, monitor sales and see your available earnings."
            icon="♫"
            accent="teal"
            onPress={() => router.push('/creator-dashboard')}
          />
        )}
        <ActionCard
          eyebrow="BEATHUB PULSE"
          title="Discover fresh sound"
          copy="Swipe through new releases, preview beats and find your next record."
          icon="♪"
          onPress={() => router.push('/(tabs)/beats')}
        />
        <ActionCard
          eyebrow="YOUR LIBRARY"
          title="Own your sound"
          copy="Access purchased tracks, licences and secure downloads in one place."
          icon="▣"
          accent="teal"
          onPress={() => router.push('/(tabs)/library')}
        />

        <View style={s.cultureCard}>
          <Text style={s.cultureKicker}>BUILT FOR THE CULTURE</Text>
          <Text style={s.cultureTitle}>Your sound deserves a <Text style={s.gold}>home.</Text></Text>
          <Text style={s.cultureCopy}>Independent artists and producers deserve tools that feel as serious as their ambition.</Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.canvas },
  container: { paddingHorizontal: 18, paddingTop: 12, paddingBottom: 36 },
  brandRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 18 },
  brandMark: { width: 36, height: 36, borderRadius: 12, backgroundColor: palette.gold, alignItems: 'center', justifyContent: 'center' },
  brandMarkText: { color: palette.ink, fontSize: 20, fontWeight: '900' },
  brand: { color: palette.white, fontWeight: '900', fontSize: 19, letterSpacing: -0.6, marginLeft: 10 },
  brandGold: { color: palette.gold },
  livePill: { marginLeft: 'auto', flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: 1, borderColor: palette.border, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 7, backgroundColor: palette.surface },
  liveDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: palette.teal },
  liveText: { color: palette.muted, fontSize: 9, fontWeight: '900', letterSpacing: 1.2 },
  hero: { overflow: 'hidden', borderRadius: radius.lg, borderWidth: 1, borderColor: palette.borderGold, backgroundColor: '#15101D', padding: 22, ...shadow },
  heroGlow: { position: 'absolute', width: 220, height: 220, borderRadius: 110, backgroundColor: 'rgba(255,184,0,0.08)', right: -80, top: -85 },
  eyebrow: { color: palette.gold, fontSize: 10, fontWeight: '900', letterSpacing: 2.2 },
  greeting: { color: palette.muted, fontSize: 13, marginTop: 14, marginBottom: 9 },
  heroTitle: { color: palette.white, fontSize: 35, lineHeight: 37, fontWeight: '900', letterSpacing: -1.8 },
  gold: { color: palette.gold },
  teal: { color: palette.teal },
  heroCopy: { color: palette.muted, fontSize: 14, lineHeight: 21, marginTop: 15, maxWidth: 320 },
  waveform: { height: 78, flexDirection: 'row', alignItems: 'center', gap: 5, marginVertical: 20 },
  waveBar: { flex: 1, maxWidth: 8, borderRadius: 8, opacity: 0.85 },
  primaryButton: { height: 52, borderRadius: radius.sm, backgroundColor: palette.gold, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 12 },
  primaryText: { color: palette.ink, fontWeight: '900', fontSize: 13, letterSpacing: 1 },
  primaryArrow: { color: palette.ink, fontSize: 20, fontWeight: '900' },
  pressed: { opacity: 0.82, transform: [{ scale: 0.985 }] },
  statRow: { flexDirection: 'row', gap: 9, marginTop: 12 },
  stat: { flex: 1, borderWidth: 1, borderColor: palette.border, borderRadius: radius.sm, backgroundColor: palette.surface, padding: 13 },
  statNumber: { color: palette.white, fontSize: 18, fontWeight: '900' },
  statLabel: { color: palette.muted2, fontSize: 8, fontWeight: '900', letterSpacing: 0.8, marginTop: 3 },
  sectionHeader: { marginTop: 30, marginBottom: 13 },
  sectionKicker: { color: palette.gold, fontSize: 9, fontWeight: '900', letterSpacing: 2 },
  sectionTitle: { color: palette.white, fontSize: 24, fontWeight: '900', letterSpacing: -0.8, marginTop: 5 },
  actionCard: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 17, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, marginBottom: 11 },
  iconBox: { width: 48, height: 48, borderRadius: 15, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  icon: { fontSize: 23, fontWeight: '900' },
  actionCopy: { flex: 1 },
  actionEyebrow: { fontSize: 8, fontWeight: '900', letterSpacing: 1.4 },
  actionTitle: { color: palette.white, fontSize: 16, fontWeight: '900', marginTop: 3 },
  actionText: { color: palette.muted, fontSize: 12, lineHeight: 17, marginTop: 5 },
  arrow: { color: palette.muted2, fontSize: 28, fontWeight: '300' },
  cultureCard: { marginTop: 18, borderRadius: radius.lg, borderWidth: 1, borderColor: 'rgba(24,213,170,0.22)', backgroundColor: '#0D1717', padding: 24 },
  cultureKicker: { color: palette.teal, fontSize: 9, fontWeight: '900', letterSpacing: 2 },
  cultureTitle: { color: palette.white, fontSize: 27, fontWeight: '900', lineHeight: 30, letterSpacing: -1, marginTop: 9 },
  cultureCopy: { color: palette.muted, fontSize: 13, lineHeight: 20, marginTop: 10 },
});
