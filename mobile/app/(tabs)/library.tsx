import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import { ActivityIndicator, Alert, FlatList, Pressable, RefreshControl, SafeAreaView, StyleSheet, Text, View } from 'react-native';
import { API_BASE_URL, getToken, api, Order } from '../../src/api';

function safeFileName(order: Order) {
  const base = (order.track_title || order.track_slug || order.order_number || 'beathub-track').replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/-+/g, '-');
  return `${base}.mp3`;
}

export default function Library(){
  const [items,setItems]=useState<Order[]>([]);
  const [busy,setBusy]=useState(true);
  const [refreshing,setRefreshing]=useState(false);
  const [downloading,setDownloading]=useState<string|null>(null);
  const [error,setError]=useState('');

  const load=useCallback(async()=>{
    try{
      const r=await api<{items:Order[]}>('/orders');
      setItems(r.items);
      setError('');
    }catch(e){ setError(e instanceof Error?e.message:'Unable to load your library.'); }
    finally{ setBusy(false); setRefreshing(false); }
  },[]);

  useFocusEffect(useCallback(()=>{load()},[load]));

  const download=useCallback(async(order:Order)=>{
    if(order.status!=='completed') return;
    setDownloading(order.id);
    try{
      const token=await getToken();
      if(!token) throw new Error('Please sign in again to download your purchase.');
      const directory=FileSystem.documentDirectory || FileSystem.cacheDirectory;
      if(!directory) throw new Error('Device storage is unavailable.');
      const target=`${directory}${safeFileName(order)}`;
      const result=await FileSystem.downloadAsync(
        `${API_BASE_URL}/orders/${encodeURIComponent(order.id)}/download`,
        target,
        {headers:{Authorization:`Bearer ${token}`}},
      );
      if(result.status<200||result.status>=400) throw new Error(`Download failed (${result.status}).`);
      if(await Sharing.isAvailableAsync()){
        await Sharing.shareAsync(result.uri,{dialogTitle:'Save or share your BeatHub purchase',mimeType:result.mimeType || 'audio/mpeg'});
      }else{
        Alert.alert('Download complete',`Saved inside BeatHub storage as ${safeFileName(order)}.`);
      }
    }catch(e){ Alert.alert('Download unavailable',e instanceof Error?e.message:'Unable to download this purchase.'); }
    finally{setDownloading(null)}
  },[]);

  return <SafeAreaView style={s.safe}><View style={s.container}>
    <Text style={s.title}>Library</Text>
    {busy?<ActivityIndicator/>:error?<View><Text style={s.error}>{error}</Text><Pressable style={s.retry} onPress={load}><Text style={s.retryText}>Try again</Text></Pressable></View>:
      <FlatList data={items} keyExtractor={x=>x.id} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={()=>{setRefreshing(true);load();}} />} contentContainerStyle={items.length?undefined:s.emptyContainer} ListEmptyComponent={<Text style={s.empty}>Your purchased beats will appear here.</Text>} renderItem={({item})=><View style={s.item}><View style={{flex:1}}><Text style={s.name}>{item.track_title||'BeatHub purchase'}</Text><Text style={s.meta}>{item.order_number} · {item.currency} {Number(item.amount||0).toFixed(2)}</Text></View><View style={s.actions}><Text style={item.status==='completed'?s.done:s.pending}>{item.status}</Text>{item.status==='completed'&&<Pressable disabled={downloading===item.id} onPress={()=>download(item)} style={s.download}>{downloading===item.id?<ActivityIndicator/>:<Text style={s.downloadText}>Download</Text>}</Pressable>}</View></View>}/>
    }
  </View></SafeAreaView>
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{flex:1,padding:20},title:{fontSize:30,fontWeight:'800',color:'#fff',marginTop:15,marginBottom:22},item:{flexDirection:'row',alignItems:'center',paddingVertical:17,borderBottomWidth:1,borderBottomColor:'#211d28'},name:{color:'#fff',fontSize:16,fontWeight:'700'},meta:{color:'#817b8b',fontSize:12,marginTop:5},actions:{alignItems:'flex-end',gap:8},done:{color:'#b8f5c7',fontWeight:'700',textTransform:'capitalize'},pending:{color:'#f1c97b',fontWeight:'700',textTransform:'capitalize'},download:{minWidth:82,paddingHorizontal:12,paddingVertical:8,borderRadius:8,backgroundColor:'#fff',alignItems:'center'},downloadText:{color:'#0d0b12',fontSize:12,fontWeight:'800'},emptyContainer:{flexGrow:1,justifyContent:'center'},empty:{color:'#8d8798',textAlign:'center'},error:{color:'#ff8f8f'},retry:{backgroundColor:'#fff',padding:12,borderRadius:10,alignItems:'center',marginTop:14},retryText:{fontWeight:'800',color:'#0d0b12'}});
