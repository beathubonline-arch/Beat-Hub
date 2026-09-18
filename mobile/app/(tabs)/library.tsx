import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import { ActivityIndicator, Alert, FlatList, Pressable, RefreshControl, SafeAreaView, StyleSheet, Text, View } from 'react-native';
import { API_BASE_URL, getToken, api, Order } from '../../src/api';
import { palette, radius } from '../../src/theme';

function safeFileName(order: Order) {
  const base = (order.track_title || order.track_slug || order.order_number || 'beathub-track').replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/-+/g, '-');
  return `${base}.mp3`;
}

export default function Library() {
  const [items, setItems] = useState<Order[]>([]);
  const [busy, setBusy] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const result = await api<{ items: Order[] }>('/orders');
      setItems(result.items);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load your library.');
    } finally {
      setBusy(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const download = useCallback(async (order: Order) => {
    if (order.status !== 'completed') return;
    setDownloading(order.id);
    try {
      const token = await getToken();
      if (!token) throw new Error('Please sign in again to download your purchase.');
      const directory = FileSystem.documentDirectory || FileSystem.cacheDirectory;
      if (!directory) throw new Error('Device storage is unavailable.');
      const target = `${directory}${safeFileName(order)}`;
      const result = await FileSystem.downloadAsync(
        `${API_BASE_URL}/orders/${encodeURIComponent(order.id)}/download`,
        target,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (result.status < 200 || result.status >= 400) throw new Error(`Download failed (${result.status}).`);
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(result.uri, { dialogTitle: 'Save or share your BeatHub purchase', mimeType: result.mimeType || 'audio/mpeg' });
      } else {
        Alert.alert('Download complete', `Saved inside BeatHub storage as ${safeFileName(order)}.`);
      }
    } catch (e) {
      Alert.alert('Download unavailable', e instanceof Error ? e.message : 'Unable to download this purchase.');
    } finally {
      setDownloading(null);
    }
  }, []);

  const completed = items.filter(item => item.status === 'completed').length;

  return (
    <SafeAreaView style={s.safe}>
      <View style={s.container}>
        <Text style={s.kicker}>YOUR MUSIC</Text>
        <Text style={s.title}>Library</Text>
        <Text style={s.subtitle}>Purchased beats, licences and secure downloads.</Text>

        <View style={s.summary}>
          <View><Text style={s.summaryValue}>{completed}</Text><Text style={s.summaryLabel}>OWNED TRACKS</Text></View>
          <View style={s.summaryDivider} />
          <View><Text style={s.summaryValue}>{items.length}</Text><Text style={s.summaryLabel}>TOTAL ORDERS</Text></View>
          <View style={s.summaryMark}><Text style={s.summaryMarkText}>♪</Text></View>
        </View>

        {busy ? (
          <View style={s.center}><ActivityIndicator color={palette.gold} /><Text style={s.centerText}>Loading your music…</Text></View>
        ) : error ? (
          <View style={s.center}><Text style={s.error}>{error}</Text><Pressable style={s.retry} onPress={load}><Text style={s.retryText}>TRY AGAIN</Text></Pressable></View>
        ) : (
          <FlatList
            data={items}
            keyExtractor={item => item.id}
            showsVerticalScrollIndicator={false}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={palette.gold} />}
            contentContainerStyle={items.length ? s.list : s.emptyContainer}
            ListEmptyComponent={
              <View style={s.emptyCard}>
                <Text style={s.emptyIcon}>♪</Text>
                <Text style={s.emptyTitle}>Your collection starts here.</Text>
                <Text style={s.empty}>Purchased beats and their licences will appear here automatically.</Text>
              </View>
            }
            renderItem={({ item, index }) => (
              <View style={s.item}>
                <View style={s.trackNumber}><Text style={s.trackNumberText}>{String(index + 1).padStart(2, '0')}</Text></View>
                <View style={s.trackCopy}>
                  <Text style={s.name} numberOfLines={1}>{item.track_title || 'BeatHub purchase'}</Text>
                  <Text style={s.meta} numberOfLines={1}>{item.order_number} · {item.currency} {Number(item.amount || 0).toLocaleString()}</Text>
                  <View style={s.statusRow}>
                    <View style={[s.statusDot, { backgroundColor: item.status === 'completed' ? palette.teal : palette.gold }]} />
                    <Text style={[s.status, { color: item.status === 'completed' ? palette.success : palette.goldBright }]}>{item.status}</Text>
                  </View>
                </View>
                {item.status === 'completed' && (
                  <Pressable disabled={downloading === item.id} onPress={() => download(item)} style={({ pressed }) => [s.download, pressed && s.pressed]}>
                    {downloading === item.id ? <ActivityIndicator color={palette.ink} size="small" /> : <><Text style={s.downloadIcon}>↓</Text><Text style={s.downloadText}>SAVE</Text></>}
                  </Pressable>
                )}
              </View>
            )}
          />
        )}
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.canvas },
  container: { flex: 1, paddingHorizontal: 18, paddingTop: 18 },
  kicker: { color: palette.gold, fontSize: 9, fontWeight: '900', letterSpacing: 2.2 },
  title: { color: palette.white, fontSize: 34, fontWeight: '900', letterSpacing: -1.3, marginTop: 4 },
  subtitle: { color: palette.muted, fontSize: 13, marginTop: 6, marginBottom: 18 },
  summary: { position: 'relative', overflow: 'hidden', flexDirection: 'row', alignItems: 'center', gap: 20, borderWidth: 1, borderColor: palette.borderGold, borderRadius: radius.md, backgroundColor: '#18121F', padding: 18, marginBottom: 15 },
  summaryValue: { color: palette.white, fontSize: 23, fontWeight: '900' },
  summaryLabel: { color: palette.muted2, fontSize: 8, fontWeight: '900', letterSpacing: 1, marginTop: 3 },
  summaryDivider: { height: 35, width: 1, backgroundColor: palette.border },
  summaryMark: { marginLeft: 'auto', width: 48, height: 48, borderRadius: 24, backgroundColor: 'rgba(255,184,0,0.12)', alignItems: 'center', justifyContent: 'center' },
  summaryMarkText: { color: palette.gold, fontSize: 25, fontWeight: '900' },
  list: { paddingBottom: 28, gap: 10 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 12, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, padding: 14 },
  trackNumber: { width: 39, height: 39, borderRadius: 12, backgroundColor: palette.surfaceRaised, alignItems: 'center', justifyContent: 'center' },
  trackNumberText: { color: palette.gold, fontSize: 11, fontWeight: '900' },
  trackCopy: { flex: 1 },
  name: { color: palette.white, fontSize: 15, fontWeight: '900' },
  meta: { color: palette.muted2, fontSize: 10, marginTop: 4 },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 7 },
  statusDot: { width: 5, height: 5, borderRadius: 3 },
  status: { fontSize: 8, fontWeight: '900', textTransform: 'uppercase', letterSpacing: 0.8 },
  download: { minWidth: 55, height: 50, borderRadius: 13, backgroundColor: palette.gold, alignItems: 'center', justifyContent: 'center' },
  downloadIcon: { color: palette.ink, fontSize: 17, lineHeight: 17, fontWeight: '900' },
  downloadText: { color: palette.ink, fontSize: 8, fontWeight: '900', marginTop: 2 },
  pressed: { opacity: 0.8 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 26 },
  centerText: { color: palette.muted, marginTop: 12 },
  error: { color: palette.danger, textAlign: 'center', lineHeight: 20 },
  retry: { backgroundColor: palette.gold, paddingHorizontal: 20, paddingVertical: 12, borderRadius: radius.sm, marginTop: 15 },
  retryText: { color: palette.ink, fontWeight: '900', fontSize: 11 },
  emptyContainer: { flexGrow: 1, justifyContent: 'center', paddingBottom: 70 },
  emptyCard: { alignItems: 'center', borderWidth: 1, borderColor: palette.border, borderRadius: radius.lg, backgroundColor: palette.surface, padding: 28 },
  emptyIcon: { color: palette.gold, fontSize: 50, fontWeight: '900' },
  emptyTitle: { color: palette.white, fontSize: 19, fontWeight: '900', marginTop: 13 },
  empty: { color: palette.muted, textAlign: 'center', lineHeight: 19, marginTop: 7 },
});
