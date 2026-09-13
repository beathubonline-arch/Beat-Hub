import { useEffect, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { ActivityIndicator, Image, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { api, Track } from '../../src/api';

function currencyLabel(currency?: string | null) {
  const value = String(currency || 'KES').trim().toUpperCase();
  return value === 'USD' ? '$' : value === 'KES' ? 'KSh' : value;
}

function timeLabel(seconds: number) {
  const safe = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

function PreviewPlayer({ url }: { url: string }) {
  const player = useAudioPlayer(url, { updateInterval: 500, downloadFirst: false });
  const status = useAudioPlayerStatus(player);

  const toggle = async () => {
    if (status.playing) {
      player.pause();
      return;
    }
    if (status.duration > 0 && status.currentTime >= status.duration - 0.2) await player.seekTo(0);
    player.play();
  };

  const progress = status.duration > 0 ? Math.min(1, status.currentTime / status.duration) : 0;
  return (
    <View style={s.playerCard}>
      <Pressable style={s.preview} onPress={toggle} disabled={!status.isLoaded && status.isBuffering}>
        {status.isBuffering ? <ActivityIndicator /> : <Text style={s.previewText}>{status.playing ? '❚❚ Pause preview' : '▶ Play preview'}</Text>}
      </Pressable>
      <View style={s.progressTrack}><View style={[s.progressFill, { width: `${progress * 100}%` }]} /></View>
      <View style={s.timeRow}><Text style={s.time}>{timeLabel(status.currentTime)}</Text><Text style={s.time}>{timeLabel(status.duration)}</Text></View>
      {!!status.error && <Text style={s.error}>Preview could not be played on this device.</Text>}
    </View>
  );
}

export default function BeatDetails() {
  const { slug } = useLocalSearchParams<{ slug: string }>();
  const [track, setTrack] = useState<Track | null>(null);
  const [busy, setBusy] = useState(true);
  const [buying, setBuying] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!slug) return;
    let active = true;
    (async () => {
      try {
        const next = await api<Track>(`/catalog/${encodeURIComponent(slug)}`);
        if (active) setTrack(next);
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : 'Unable to load track.');
      } finally {
        if (active) setBusy(false);
      }
    })();
    return () => { active = false; };
  }, [slug]);

  async function buy() {
    if (!track || track.is_sold) return;
    setBuying(true);
    setError('');
    try {
      const r = await api<{ order_id: string; reference: string; authorization_url: string }>(
        '/payments/paystack/initialize',
        { method: 'POST', body: JSON.stringify({ slug: track.slug }) },
      );
      router.push(`/checkout/${r.order_id}?url=${encodeURIComponent(r.authorization_url)}&reference=${encodeURIComponent(r.reference)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to start checkout.');
    } finally {
      setBuying(false);
    }
  }

  if (busy) return <SafeAreaView style={s.safe}><View style={s.center}><ActivityIndicator /></View></SafeAreaView>;
  if (!track) return <SafeAreaView style={s.safe}><View style={s.container}><Text style={s.error}>{error || 'Track not found.'}</Text></View></SafeAreaView>;

  const symbol = currencyLabel(track.currency);
  const price = Number(track.price || 0).toFixed(2);
  return (
    <SafeAreaView style={s.safe}>
      <ScrollView contentContainerStyle={s.container}>
        <Pressable onPress={() => router.back()}><Text style={s.back}>‹ Back</Text></Pressable>
        <View style={s.art}>{track.artwork_url ? <Image source={{ uri: track.artwork_url }} style={s.artImage} resizeMode="cover" /> : <Text style={s.note}>♪</Text>}</View>
        <Text style={s.title}>{track.title}</Text>
        <Text style={s.producer}>{track.producer || 'BeatHub Creator'}</Text>
        <View style={s.meta}>
          <Text style={s.metaText}>{track.genre || 'Music'}</Text>
          <Text style={s.metaText}>{track.bpm ? `${track.bpm} BPM` : 'Beat'}</Text>
          <Text style={s.metaText}>{track.sales_model === 'non_exclusive' ? 'Non-exclusive' : 'Exclusive'}</Text>
        </View>
        {!!track.description && <Text style={s.description}>{track.description}</Text>}
        {!!track.preview_url && <PreviewPlayer url={track.preview_url} />}
        {!!error && <Text style={s.error}>{error}</Text>}
        <Pressable style={[s.buy, track.is_sold && s.disabled]} onPress={buy} disabled={buying || track.is_sold}>
          {buying ? <ActivityIndicator /> : <Text style={s.buyText}>{track.is_sold ? 'Sold' : `Buy · ${symbol} ${price}`}</Text>}
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0d0b12' }, center:{flex:1,alignItems:'center',justifyContent:'center'}, container: { padding: 22, paddingBottom: 50 },
  back: { color: '#aaa3b4', fontSize: 16, marginVertical: 12 }, art: { height: 300, borderRadius: 22, backgroundColor: '#211c29', overflow: 'hidden', alignItems: 'center', justifyContent: 'center', marginBottom: 24 },
  artImage: { width: '100%', height: '100%' }, note: { fontSize: 80, color: '#fff' }, title: { fontSize: 31, fontWeight: '800', color: '#fff' }, producer: { fontSize: 16, color: '#aaa3b4', marginTop: 6 },
  meta: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 18 }, metaText: { color: '#ddd6e4', backgroundColor: '#181520', padding: 8, borderRadius: 8, fontSize: 12 }, description: { color: '#aaa3b4', lineHeight: 23, marginTop: 22 },
  playerCard:{backgroundColor:'#181520',borderRadius:16,padding:14,marginTop:24}, preview: { borderWidth: 1, borderColor: '#4b4354', padding: 14, borderRadius: 12, alignItems: 'center' }, previewText: { color: '#fff', fontWeight: '800' },
  progressTrack:{height:5,borderRadius:5,backgroundColor:'#302b38',overflow:'hidden',marginTop:14},progressFill:{height:'100%',backgroundColor:'#fff'},timeRow:{flexDirection:'row',justifyContent:'space-between',marginTop:7},time:{color:'#817b8b',fontSize:11},
  buy: { backgroundColor: '#fff', padding: 17, borderRadius: 13, alignItems: 'center', marginTop: 14 }, disabled: { opacity: 0.45 }, buyText: { fontWeight: '800', color: '#0d0b12' }, error: { color: '#ff8f8f', paddingVertical: 12 },
});
