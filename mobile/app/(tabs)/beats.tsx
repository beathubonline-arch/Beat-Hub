import { useCallback, useState } from 'react';
import { router, useFocusEffect } from 'expo-router';
import { ActivityIndicator, Image, FlatList, Pressable, SafeAreaView, StyleSheet, Text, TextInput, View } from 'react-native';
import { api, Track } from '../../src/api';

function currencyLabel(currency?: string | null) {
  const value = String(currency || 'KES').trim().toUpperCase();
  return value === 'USD' ? '$' : value === 'KES' ? 'KSh' : value;
}

function priceLabel(track: Track) {
  return `${currencyLabel(track.currency)} ${Number(track.price || 0).toFixed(2)}`;
}

export default function Beats() {
  const [items, setItems] = useState<Track[]>([]);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api<{ items: Track[] }>(`/catalog?q=${encodeURIComponent(q)}`);
      setItems(r.items);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load beats.');
    } finally {
      setBusy(false);
    }
  }, [q]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  return (
    <SafeAreaView style={s.safe}>
      <View style={s.container}>
        <Text style={s.title}>Beats</Text>
        <TextInput
          style={s.search}
          placeholder="Search beats, genres..."
          placeholderTextColor="#777180"
          value={q}
          onChangeText={setQ}
          onSubmitEditing={load}
          returnKeyType="search"
        />
        {busy ? <ActivityIndicator /> : error ? <Text style={s.error}>{error}</Text> : (
          <FlatList
            data={items}
            keyExtractor={x => x.id}
            contentContainerStyle={items.length ? undefined : s.emptyContainer}
            ListEmptyComponent={<Text style={s.empty}>No beats found.</Text>}
            renderItem={({ item }) => (
              <Pressable style={s.item} onPress={() => router.push(`/beat/${item.slug}`)}>
                <View style={s.art}>
                  {item.artwork_url ? <Image source={{ uri: item.artwork_url }} style={s.artImage} /> : <Text style={s.note}>♪</Text>}
                </View>
                <View style={s.info}>
                  <Text style={s.name} numberOfLines={1}>{item.title}</Text>
                  <Text style={s.meta} numberOfLines={1}>{item.producer || 'BeatHub Creator'} · {item.genre || 'Music'}</Text>
                  <Text style={s.sales}>{item.is_sold ? 'Sold' : item.sales_model}</Text>
                </View>
                <Text style={s.price}>{priceLabel(item)}</Text>
              </Pressable>
            )}
          />
        )}
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0d0b12' },
  container: { flex: 1, padding: 20 },
  title: { fontSize: 30, fontWeight: '800', color: '#fff', marginTop: 15, marginBottom: 16 },
  search: { backgroundColor: '#181520', color: '#fff', padding: 15, borderRadius: 12, marginBottom: 15 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#211d28' },
  art: { width: 58, height: 58, borderRadius: 10, backgroundColor: '#25202d', overflow: 'hidden', alignItems: 'center', justifyContent: 'center' },
  artImage: { width: '100%', height: '100%' },
  note: { color: '#fff', fontSize: 25 },
  info: { flex: 1, minWidth: 0 },
  name: { color: '#fff', fontSize: 16, fontWeight: '700' },
  meta: { color: '#817b8b', fontSize: 13, marginTop: 4 },
  sales: { color: '#aaa3b4', fontSize: 11, marginTop: 4, textTransform: 'capitalize' },
  price: { color: '#fff', fontWeight: '700' },
  error: { color: '#ff8f8f' },
  emptyContainer: { flexGrow: 1, justifyContent: 'center' },
  empty: { color: '#817b8b', textAlign: 'center' },
});
