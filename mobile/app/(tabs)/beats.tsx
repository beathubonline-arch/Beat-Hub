import { useCallback, useState } from 'react';
import { router, useFocusEffect } from 'expo-router';
import { ActivityIndicator, Image, FlatList, Pressable, RefreshControl, SafeAreaView, StyleSheet, Text, TextInput, View } from 'react-native';
import { api, Track } from '../../src/api';

function currencyLabel(currency?: string | null) {
  const value = String(currency || 'KES').trim().toUpperCase();
  return value === 'USD' ? '$' : value === 'KES' ? 'KSh' : value;
}
function priceLabel(track: Track) { return `${currencyLabel(track.currency)} ${Number(track.price || 0).toFixed(2)}`; }

type Catalog={items:Track[];page:number;limit:number;total:number};
export default function Beats() {
  const [items, setItems] = useState<Track[]>([]);
  const [q, setQ] = useState(''); const [search, setSearch] = useState('');
  const [page,setPage]=useState(1); const [total,setTotal]=useState(0);
  const [busy, setBusy] = useState(true); const [refreshing,setRefreshing]=useState(false); const [loadingMore,setLoadingMore]=useState(false); const [error, setError] = useState('');

  const load = useCallback(async (nextPage=1,append=false) => {
    if(nextPage===1&&!refreshing)setBusy(true); else if(nextPage>1)setLoadingMore(true);
    try {
      const r = await api<Catalog>(`/catalog?q=${encodeURIComponent(search)}&page=${nextPage}&limit=20`);
      setItems(current=>append?[...current,...r.items.filter(item=>!current.some(x=>x.id===item.id))]:r.items);
      setPage(r.page); setTotal(r.total); setError('');
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load beats.'); }
    finally { setBusy(false);setRefreshing(false);setLoadingMore(false); }
  }, [search,refreshing]);

  useFocusEffect(useCallback(() => { load(1,false); }, [load]));
  const submitSearch=()=>{setSearch(q.trim());setPage(1);};
  const refresh=()=>{setRefreshing(true);load(1,false);};
  const more=()=>{if(!busy&&!loadingMore&&items.length<total)load(page+1,true);};

  return <SafeAreaView style={s.safe}><View style={s.container}>
    <Text style={s.title}>Beats</Text>
    <View style={s.searchRow}><TextInput style={s.search} placeholder="Search beats, genres..." placeholderTextColor="#777180" value={q} onChangeText={setQ} onSubmitEditing={submitSearch} returnKeyType="search"/><Pressable style={s.searchButton} onPress={submitSearch}><Text style={s.searchButtonText}>Search</Text></Pressable></View>
    {busy ? <ActivityIndicator /> : error ? <View><Text style={s.error}>{error}</Text><Pressable style={s.retry} onPress={()=>load(1,false)}><Text style={s.retryText}>Try again</Text></Pressable></View> : <FlatList data={items} keyExtractor={x => x.id} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh}/>} onEndReached={more} onEndReachedThreshold={0.35} ListFooterComponent={loadingMore?<ActivityIndicator style={{margin:18}}/>:null} contentContainerStyle={items.length ? undefined : s.emptyContainer} ListEmptyComponent={<Text style={s.empty}>No beats found.</Text>} renderItem={({ item }) => <Pressable style={s.item} onPress={() => router.push(`/beat/${item.slug}`)}><View style={s.art}>{item.artwork_url ? <Image source={{ uri: item.artwork_url }} style={s.artImage} /> : <Text style={s.note}>♪</Text>}</View><View style={s.info}><Text style={s.name} numberOfLines={1}>{item.title}</Text><Text style={s.meta} numberOfLines={1}>{item.producer || 'BeatHub Creator'} · {item.genre || 'Music'}</Text><Text style={s.sales}>{item.is_sold ? 'Sold' : item.sales_model.replace('_',' ')}</Text></View><Text style={s.price}>{priceLabel(item)}</Text></Pressable>} />}
  </View></SafeAreaView>;
}
const s = StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{flex:1,padding:20},title:{fontSize:30,fontWeight:'800',color:'#fff',marginTop:15,marginBottom:16},searchRow:{flexDirection:'row',gap:8,marginBottom:15},search:{flex:1,backgroundColor:'#181520',color:'#fff',padding:15,borderRadius:12},searchButton:{backgroundColor:'#fff',paddingHorizontal:14,justifyContent:'center',borderRadius:12},searchButtonText:{color:'#0d0b12',fontWeight:'800'},item:{flexDirection:'row',alignItems:'center',gap:12,paddingVertical:12,borderBottomWidth:1,borderBottomColor:'#211d28'},art:{width:58,height:58,borderRadius:10,backgroundColor:'#25202d',overflow:'hidden',alignItems:'center',justifyContent:'center'},artImage:{width:'100%',height:'100%'},note:{color:'#fff',fontSize:25},info:{flex:1,minWidth:0},name:{color:'#fff',fontSize:16,fontWeight:'700'},meta:{color:'#817b8b',fontSize:13,marginTop:4},sales:{color:'#aaa3b4',fontSize:11,marginTop:4,textTransform:'capitalize'},price:{color:'#fff',fontWeight:'700'},error:{color:'#ff8f8f'},retry:{backgroundColor:'#fff',padding:12,borderRadius:10,alignItems:'center',marginTop:14},retryText:{color:'#0d0b12',fontWeight:'800'},emptyContainer:{flexGrow:1,justifyContent:'center'},empty:{color:'#817b8b',textAlign:'center'}});
