import AsyncStorage from '@react-native-async-storage/async-storage';
import { router, useFocusEffect } from 'expo-router';
import { useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  SafeAreaView,
  Share,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import { api, Track } from '../../src/api';

const SAVED_KEY = 'beathub_pulse_saved_tracks';

function currencyLabel(currency?: string | null) {
  const value = String(currency || 'KES').trim().toUpperCase();
  return value === 'USD' ? '$' : value === 'KES' ? 'KSh' : value;
}

function priceLabel(track: Track) {
  return `${currencyLabel(track.currency)} ${Number(track.price || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function timeLabel(seconds: number) {
  const safe = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

type Catalog = { items: Track[]; page: number; limit: number; total: number };

type PulseCardProps = {
  track: Track;
  active: boolean;
  height: number;
  saved: boolean;
  onSave: () => void;
};

function PulseCard({ track, active, height, saved, onSave }: PulseCardProps) {
  const player = useAudioPlayer(track.preview_url || null, { updateInterval: 350, downloadFirst: false });
  const status = useAudioPlayerStatus(player);

  useEffect(() => {
    if (!track.preview_url || !status.isLoaded) return;
    if (active) player.play();
    else player.pause();
  }, [active, player, status.isLoaded, track.preview_url]);

  const togglePreview = async () => {
    if (!track.preview_url) return;
    if (status.playing) {
      player.pause();
      return;
    }
    if (status.duration > 0 && status.currentTime >= status.duration - 0.2) {
      await player.seekTo(0);
    }
    player.play();
  };

  const share = async () => {
    const url = track.track_url || `https://mybeathub.com/track/${track.slug}`;
    await Share.share({
      message: `${track.title} by ${track.producer || 'a BeatHub creator'}\nListen and license it on BeatHub: ${url}`,
      url,
      title: track.title,
    });
  };

  const progress = status.duration > 0 ? Math.min(1, status.currentTime / status.duration) : 0;
  const exclusive = track.sales_model === 'exclusive';

  return (
    <View style={[s.card, { height }]}>
      {track.artwork_url ? (
        <Image source={{ uri: track.artwork_url }} style={s.artwork} resizeMode="cover" />
      ) : (
        <View style={s.fallbackArtwork}><Text style={s.fallbackNote}>♪</Text></View>
      )}
      <View style={s.scrim} />

      <View style={s.topBadges}>
        <View style={s.genreBadge}><Text style={s.genreText}>{track.genre || 'Beat'}</Text></View>
        {!!track.bpm && <View style={s.bpmBadge}><Text style={s.bpmText}>{track.bpm} BPM</Text></View>}
      </View>

      <View style={s.sideActions}>
        <Pressable accessibilityLabel={saved ? 'Remove from saved beats' : 'Save beat'} style={s.roundAction} onPress={onSave}>
          <Text style={[s.actionIcon, saved && s.savedIcon]}>{saved ? '♥' : '♡'}</Text>
          <Text style={s.actionLabel}>Save</Text>
        </Pressable>
        <Pressable accessibilityLabel="Share beat" style={s.roundAction} onPress={share}>
          <Text style={s.actionIcon}>↗</Text>
          <Text style={s.actionLabel}>Share</Text>
        </Pressable>
      </View>

      <View style={s.details}>
        <Pressable onPress={() => router.push(`/beat/${track.slug}`)}>
          <Text style={s.trackTitle} numberOfLines={2}>{track.title}</Text>
        </Pressable>
        <View style={s.producerRow}>
          <Text style={s.producer} numberOfLines={1}>@{track.producer || 'BeatHub Creator'}</Text>
          {track.producer_verified && <View style={s.verifiedBadge}><Text style={s.verifiedText}>✓ ACCOUNT VERIFIED</Text></View>}
        </View>

        {exclusive && !track.is_sold && (
          <Text style={s.scarcity}>1 exclusive licence available · removed after purchase</Text>
        )}

        <View style={s.playerRow}>
          <Pressable
            accessibilityLabel={status.playing ? 'Pause preview' : 'Play preview'}
            style={[s.playButton, !track.preview_url && s.disabled]}
            onPress={togglePreview}
            disabled={!track.preview_url}
          >
            {status.isBuffering ? <ActivityIndicator color="#111018" /> : <Text style={s.playText}>{status.playing ? 'Ⅱ' : '▶'}</Text>}
          </Pressable>
          <View style={s.progressArea}>
            <View style={s.progressTrack}><View style={[s.progressFill, { width: `${progress * 100}%` }]} /></View>
            <View style={s.timeRow}>
              <Text style={s.time}>{timeLabel(status.currentTime)}</Text>
              <Text style={s.time}>{timeLabel(status.duration)}</Text>
            </View>
          </View>
        </View>

        <View style={s.purchaseRow}>
          <View>
            <Text style={s.licence}>{exclusive ? 'EXCLUSIVE LICENCE' : 'NON-EXCLUSIVE LICENCE'}</Text>
            <Text style={s.price}>{priceLabel(track)}</Text>
          </View>
          <Pressable
            style={[s.buyButton, track.is_sold && s.disabled]}
            disabled={track.is_sold}
            onPress={() => router.push(`/beat/${track.slug}`)}
          >
            <Text style={s.buyText}>{track.is_sold ? 'SOLD' : 'VIEW & BUY'}</Text>
          </Pressable>
        </View>
        {!!status.error && <Text style={s.audioError}>Preview unavailable on this device.</Text>}
      </View>
    </View>
  );
}

export default function Beats() {
  const { height: windowHeight } = useWindowDimensions();
  const cardHeight = Math.max(470, windowHeight - 205);
  const [items, setItems] = useState<Track[]>([]);
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    AsyncStorage.getItem(SAVED_KEY)
      .then(value => {
        const parsed = value ? JSON.parse(value) : [];
        if (Array.isArray(parsed)) setSavedIds(new Set(parsed.filter(id => typeof id === 'string')));
      })
      .catch(() => {});
  }, []);

  const load = useCallback(async (nextPage = 1, append = false, asRefresh = false) => {
    if (nextPage === 1 && !asRefresh) setBusy(true);
    else if (nextPage > 1) setLoadingMore(true);
    try {
      const result = await api<Catalog>(`/catalog?q=${encodeURIComponent(search)}&page=${nextPage}&limit=20`);
      setItems(current => append
        ? [...current, ...result.items.filter(item => !current.some(existing => existing.id === item.id))]
        : result.items);
      setPage(result.page);
      setTotal(result.total);
      setActiveId(current => current || result.items[0]?.id || null);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load BeatHub Pulse.');
    } finally {
      setBusy(false);
      setRefreshing(false);
      setLoadingMore(false);
    }
  }, [search]);

  useFocusEffect(useCallback(() => {
    load(1, false);
    return () => setActiveId(null);
  }, [load]));

  const submitSearch = () => {
    setSearch(q.trim());
    setPage(1);
    setActiveId(null);
  };

  const refresh = () => {
    setRefreshing(true);
    setActiveId(null);
    load(1, false, true);
  };

  const loadMore = () => {
    if (!busy && !loadingMore && items.length < total) load(page + 1, true);
  };

  const toggleSaved = useCallback((trackId: string) => {
    setSavedIds(current => {
      const next = new Set(current);
      if (next.has(trackId)) next.delete(trackId);
      else next.add(trackId);
      AsyncStorage.setItem(SAVED_KEY, JSON.stringify([...next])).catch(() => {});
      return next;
    });
  }, []);

  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: Array<{ item: Track; isViewable: boolean }> }) => {
    const first = viewableItems.find(entry => entry.isViewable);
    setActiveId(first?.item.id || null);
  }).current;
  const viewabilityConfig = useRef({ itemVisiblePercentThreshold: 72 }).current;

  return (
    <SafeAreaView style={s.safe}>
      <View style={s.header}>
        <View>
          <Text style={s.eyebrow}>DISCOVER AFRICAN SOUND</Text>
          <Text style={s.title}>BeatHub Pulse</Text>
        </View>
        <Text style={s.swipeHint}>SWIPE ↑</Text>
      </View>
      <View style={s.searchRow}>
        <TextInput
          style={s.search}
          placeholder="Search sound, genre or producer"
          placeholderTextColor="#797383"
          value={q}
          onChangeText={setQ}
          onSubmitEditing={submitSearch}
          returnKeyType="search"
        />
        <Pressable style={s.searchButton} onPress={submitSearch}><Text style={s.searchButtonText}>GO</Text></Pressable>
      </View>

      {busy ? (
        <View style={s.center}><ActivityIndicator color="#ffb800" /><Text style={s.loadingText}>Loading fresh sounds…</Text></View>
      ) : error ? (
        <View style={s.center}><Text style={s.error}>{error}</Text><Pressable style={s.retry} onPress={() => load(1, false)}><Text style={s.retryText}>Try again</Text></Pressable></View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={item => item.id}
          renderItem={({ item }) => (
            <PulseCard
              track={item}
              height={cardHeight}
              active={activeId === item.id}
              saved={savedIds.has(item.id)}
              onSave={() => toggleSaved(item.id)}
            />
          )}
          pagingEnabled
          snapToInterval={cardHeight + 12}
          decelerationRate="fast"
          showsVerticalScrollIndicator={false}
          onViewableItemsChanged={onViewableItemsChanged}
          viewabilityConfig={viewabilityConfig}
          onEndReached={loadMore}
          onEndReachedThreshold={0.5}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor="#ffb800" />}
          ListFooterComponent={loadingMore ? <ActivityIndicator style={s.footerLoader} color="#ffb800" /> : null}
          ListEmptyComponent={<View style={s.center}><Text style={s.emptyTitle}>No sounds found</Text><Text style={s.empty}>Try another genre, producer or title.</Text></View>}
          getItemLayout={(_, index) => ({ length: cardHeight + 12, offset: (cardHeight + 12) * index, index })}
          windowSize={3}
          initialNumToRender={2}
          maxToRenderPerBatch={3}
          contentContainerStyle={items.length ? s.list : s.emptyContainer}
        />
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0d0b12' },
  header: { paddingHorizontal: 18, paddingTop: 12, paddingBottom: 8, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' },
  eyebrow: { color: '#ffb800', fontSize: 10, fontWeight: '900', letterSpacing: 1.8 },
  title: { color: '#fff', fontSize: 29, fontWeight: '900', letterSpacing: -1, marginTop: 2 },
  swipeHint: { color: '#8f8998', fontSize: 10, fontWeight: '800', letterSpacing: 1.4, paddingBottom: 5 },
  searchRow: { flexDirection: 'row', gap: 8, paddingHorizontal: 18, paddingBottom: 10 },
  search: { flex: 1, height: 43, backgroundColor: '#181520', borderWidth: 1, borderColor: '#292432', color: '#fff', paddingHorizontal: 14, borderRadius: 13, fontSize: 14 },
  searchButton: { width: 50, height: 43, borderRadius: 13, backgroundColor: '#ffb800', alignItems: 'center', justifyContent: 'center' },
  searchButtonText: { color: '#17110a', fontWeight: '900', fontSize: 12 },
  list: { paddingHorizontal: 12, paddingBottom: 12, gap: 12 },
  card: { borderRadius: 24, overflow: 'hidden', backgroundColor: '#181520', position: 'relative' },
  artwork: { position: 'absolute', inset: 0, width: '100%', height: '100%' },
  fallbackArtwork: { position: 'absolute', inset: 0, backgroundColor: '#261d30', alignItems: 'center', justifyContent: 'center' },
  fallbackNote: { color: '#ffb800', fontSize: 110, fontWeight: '900' },
  scrim: { position: 'absolute', inset: 0, backgroundColor: 'rgba(8, 7, 12, 0.28)' },
  topBadges: { position: 'absolute', left: 16, top: 16, flexDirection: 'row', gap: 7 },
  genreBadge: { borderRadius: 999, backgroundColor: '#ffb800', paddingHorizontal: 11, paddingVertical: 7 },
  genreText: { color: '#17110a', fontSize: 10, fontWeight: '900', textTransform: 'uppercase' },
  bpmBadge: { borderRadius: 999, backgroundColor: 'rgba(13, 11, 18, 0.78)', paddingHorizontal: 11, paddingVertical: 7 },
  bpmText: { color: '#fff', fontSize: 10, fontWeight: '800' },
  sideActions: { position: 'absolute', right: 13, bottom: 185, gap: 12, alignItems: 'center' },
  roundAction: { width: 48, alignItems: 'center' },
  actionIcon: { width: 44, height: 44, borderRadius: 22, backgroundColor: 'rgba(13, 11, 18, 0.76)', color: '#fff', textAlign: 'center', textAlignVertical: 'center', fontSize: 26, overflow: 'hidden' },
  savedIcon: { color: '#ffb800' },
  actionLabel: { color: '#fff', fontSize: 10, fontWeight: '700', marginTop: 4, textShadowColor: '#000', textShadowRadius: 5 },
  details: { position: 'absolute', left: 0, right: 0, bottom: 0, padding: 18, paddingRight: 70, backgroundColor: 'rgba(10, 8, 14, 0.88)' },
  trackTitle: { color: '#fff', fontSize: 25, fontWeight: '900', letterSpacing: -0.5 },
  producerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 5 },
  producer: { color: '#d8d2df', fontSize: 14, fontWeight: '700', maxWidth: '58%' },
  verifiedBadge: { borderWidth: 1, borderColor: '#49d98a', backgroundColor: 'rgba(34, 118, 72, 0.22)', paddingHorizontal: 7, paddingVertical: 3, borderRadius: 999 },
  verifiedText: { color: '#7df0aa', fontSize: 8, fontWeight: '900', letterSpacing: 0.5 },
  scarcity: { color: '#ffcf4a', fontSize: 11, fontWeight: '800', marginTop: 8 },
  playerRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 13 },
  playButton: { width: 43, height: 43, borderRadius: 22, backgroundColor: '#fff', alignItems: 'center', justifyContent: 'center' },
  playText: { color: '#111018', fontSize: 17, fontWeight: '900' },
  progressArea: { flex: 1 },
  progressTrack: { height: 4, borderRadius: 4, backgroundColor: '#48414f', overflow: 'hidden' },
  progressFill: { height: '100%', backgroundColor: '#ffb800' },
  timeRow: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 },
  time: { color: '#aaa3b4', fontSize: 9, fontVariant: ['tabular-nums'] },
  purchaseRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginTop: 13 },
  licence: { color: '#aaa3b4', fontSize: 9, fontWeight: '800', letterSpacing: 0.6 },
  price: { color: '#fff', fontSize: 18, fontWeight: '900', marginTop: 2 },
  buyButton: { backgroundColor: '#ffb800', borderRadius: 12, paddingHorizontal: 15, paddingVertical: 12 },
  buyText: { color: '#17110a', fontSize: 11, fontWeight: '900' },
  disabled: { opacity: 0.45 },
  audioError: { color: '#ff9a9a', fontSize: 10, marginTop: 6 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 28 },
  loadingText: { color: '#aaa3b4', marginTop: 12 },
  error: { color: '#ff9a9a', textAlign: 'center', lineHeight: 21 },
  retry: { backgroundColor: '#fff', paddingHorizontal: 20, paddingVertical: 12, borderRadius: 11, marginTop: 15 },
  retryText: { color: '#0d0b12', fontWeight: '900' },
  emptyContainer: { flexGrow: 1 },
  emptyTitle: { color: '#fff', fontSize: 20, fontWeight: '800' },
  empty: { color: '#8d8798', marginTop: 7, textAlign: 'center' },
  footerLoader: { marginVertical: 20 },
});
