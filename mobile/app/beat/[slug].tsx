import { useEffect, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { ActivityIndicator, Image, Linking, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { api, Track } from '../../src/api';

function currencyLabel(currency?: string | null) {
  const value = String(currency || 'KES').trim().toUpperCase();
  return value === 'USD' ? '$' : value === 'KES' ? 'KSh' : value;
}

export default function BeatDetails() {
  const { slug } = useLocalSearchParams<{ slug: string }>();
  const [track, setTrack] = useState<Track | null>(null);
  const [busy, setBusy] = useState(true);
  const [buying, setBuying] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!slug) return;
    (async () => {
      try {
        setTrack(await api<Track>(`/catalog/${encodeURIComponent(slug)}`));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unable to load track.');
      } finally {
        setBusy(false);
      }
    })();
  }, [slug]);

  async function preview() {
    if (!track?.preview_url) return;
    setPreviewing(true);
    setError('');
    try {
      await Linking.openURL(track.preview_url);
    } catch {
      setError('Unable to open the preview audio.');
    } finally {
      setPreviewing(false);
    }
  }

  async function buy() {
    if (!track || track.is_sold) return;
    setBuying(true);
    setError('');
    try {
      const r = await api<{ order_id: string; reference: string; authorization_url: string }>(
        `/payments/paystack/initialize`,
        { method: 'POST', body: JSON.stringify({ slug: track.slug }) },
      );
      router.push(
        `/checkout/${r.order_id}?url=${encodeURIComponent(r.authorization_url)}&reference=${encodeURIComponent(r.reference)}`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to start checkout.');
    } finally {
      setBuying(false);
    }
  }

  if (busy) return <SafeAreaView style={s.safe}><ActivityIndicator /></SafeAreaView>;
  if (!track) return <SafeAreaView style={s.safe}><Text style={s.error}>{error || 'Track not found.'}</Text></SafeAreaView>;

  const symbol = currencyLabel(track.currency);
  const price = Number(track.price || 0).toFixed(2);

  return (
    <SafeAreaView style={s.safe}>
      <ScrollView contentContainerStyle={s.container}>
        <Pressable onPress={() => router.back()}><Text style={s.back}>‹ Back</Text></Pressable>
        <View style={s.art}>
          {track.artwork_url ? (
            <Image source={{ uri: track.artwork_url }} style={s.artImage} resizeMode="cover" />
          ) : <Text style={s.note}>♪</Text>}
        </View>
        <Text style={s.title}>{track.title}</Text>
        <Text style={s.producer}>{track.producer || 'BeatHub Creator'}</Text>
        <View style={s.meta}>
          <Text style={s.metaText}>{track.genre || 'Music'}</Text>
          <Text style={s.metaText}>{track.bpm ? `${track.bpm} BPM` : 'Beat'}</Text>
          <Text style={s.metaText}>{track.sales_model || 'License'}</Text>
        </View>
        {!!track.description && <Text style={s.description}>{track.description}</Text>}
        {!!track.preview_url && (
          <Pressable style={s.preview} onPress={preview} disabled={previewing}>
            {previewing ? <ActivityIndicator /> : <Text style={s.previewText}>▶ Preview beat</Text>}
          </Pressable>
        )}
        {!!error && <Text style={s.error}>{error}</Text>}
        <Pressable style={[s.buy, track.is_sold && s.disabled]} onPress={buy} disabled={buying || track.is_sold}>
          {buying ? <ActivityIndicator /> : <Text style={s.buyText}>{track.is_sold ? 'Sold' : `Buy · ${symbol} ${price}`}</Text>}
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0d0b12' },
  container: { padding: 22, paddingBottom: 50 },
  back: { color: '#aaa3b4', fontSize: 16, marginVertical: 12 },
  art: { height: 300, borderRadius: 22, backgroundColor: '#211c29', overflow: 'hidden', alignItems: 'center', justifyContent: 'center', marginBottom: 24 },
  artImage: { width: '100%', height: '100%' },
  note: { fontSize: 80, color: '#fff' },
  title: { fontSize: 31, fontWeight: '800', color: '#fff' },
  producer: { fontSize: 16, color: '#aaa3b4', marginTop: 6 },
  meta: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 18 },
  metaText: { color: '#ddd6e4', backgroundColor: '#181520', padding: 8, borderRadius: 8, fontSize: 12 },
  description: { color: '#aaa3b4', lineHeight: 23, marginTop: 22 },
  preview: { borderWidth: 1, borderColor: '#4b4354', padding: 15, borderRadius: 13, alignItems: 'center', marginTop: 24 },
  previewText: { color: '#fff', fontWeight: '800' },
  buy: { backgroundColor: '#fff', padding: 17, borderRadius: 13, alignItems: 'center', marginTop: 14 },
  disabled: { opacity: 0.45 },
  buyText: { fontWeight: '800', color: '#0d0b12' },
  error: { color: '#ff8f8f', paddingVertical: 12 },
});
