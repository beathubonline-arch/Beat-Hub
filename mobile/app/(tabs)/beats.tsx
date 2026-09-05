import { useCallback, useState } from 'react';
import { router, useFocusEffect } from 'expo-router';
import { ActivityIndicator, FlatList, Pressable, SafeAreaView, StyleSheet, Text, TextInput, View } from 'react-native';
import { api, Track } from '../../src/api';

export default function Beats() {
  const [items, setItems] = useState<Track[]>([]); const [q, setQ] = useState(''); const [busy, setBusy] = useState(true); const [error, setError] = useState('');
  const load = useCallback(async () => { setBusy(true); try { const r = await api<{ items: Track[] }>(`/catalog?q=${encodeURIComponent(q)}`); setItems(r.items); setError(''); } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load beats.'); } finally { setBusy(false); } }, [q]);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  return <SafeAreaView style={s.safe}><View style={s.container}><Text style={s.title}>Beats</Text><TextInput style={s.search} placeholder="Search beats, genres..." placeholderTextColor="#777180" value={q} onChangeText={setQ} onSubmitEditing={load} />{busy ? <ActivityIndicator /> : error ? <Text style={s.error}>{error}</Text> : <FlatList data={items} keyExtractor={x => x.id} renderItem={({ item }) => <Pressable style={s.item} onPress={() => router.push(`/beat/${item.slug}`)}><View style={s.art}><Text style={s.note}>♪</Text></View><View style={{ flex: 1 }}><Text style={s.name}>{item.title}</Text><Text style={s.meta}>{item.producer || 'BeatHub Creator'} · {item.genre || 'Music'}</Text></View><Text style={s.price}>{item.currency} {item.price.toFixed(0)}</Text></Pressable>} />} /></View></SafeAreaView>;
}
const s = StyleSheet.create({ safe:{flex:1,backgroundColor:'#0d0b12'}, container:{flex:1,padding:20}, title:{fontSize:30,fontWeight:'800',color:'#fff',marginTop:15,marginBottom:16}, search:{backgroundColor:'#181520',color:'#fff',padding:15,borderRadius:12,marginBottom:15}, item:{flexDirection:'row',alignItems:'center',gap:12,paddingVertical:12,borderBottomWidth:1,borderBottomColor:'#211d28'}, art:{width:56,height:56,borderRadius:10,backgroundColor:'#25202d',alignItems:'center',justifyContent:'center'}, note:{color:'#fff',fontSize:25}, name:{color:'#fff',fontSize:16,fontWeight:'700'}, meta:{color:'#817b8b',fontSize:13,marginTop:4}, price:{color:'#fff',fontWeight:'700'}, error:{color:'#ff8f8f'} });
