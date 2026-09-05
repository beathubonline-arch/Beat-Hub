import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { ActivityIndicator, Alert, FlatList, Linking, Pressable, SafeAreaView, StyleSheet, Text, View } from 'react-native';
import { api, API_BASE_URL, getToken, Order } from '../../src/api';

export default function Library(){
  const [items,setItems]=useState<Order[]>([]);
  const [busy,setBusy]=useState(true);
  const [downloading,setDownloading]=useState<string|null>(null);
  const [error,setError]=useState('');

  const load=useCallback(async()=>{
    setBusy(true);
    try{
      const r=await api<{items:Order[]}>('/orders');
      setItems(r.items);
      setError('');
    }catch(e){
      setError(e instanceof Error?e.message:'Unable to load your library.');
    }finally{setBusy(false)}
  },[]);

  useFocusEffect(useCallback(()=>{load()},[load]));

  const download=useCallback(async(order:Order)=>{
    if(order.status!=='completed') return;
    setDownloading(order.id);
    try{
      const token=await getToken();
      if(!token) throw new Error('Please sign in again to download your purchase.');
      const response=await fetch(`${API_BASE_URL}/orders/${encodeURIComponent(order.id)}/download`,{
        headers:{Authorization:`Bearer ${token}`},
        redirect:'follow',
      });
      if(!response.ok) throw new Error(`Download failed (${response.status})`);
      const finalUrl=response.url;
      if(!finalUrl) throw new Error('The download link could not be prepared.');
      await Linking.openURL(finalUrl);
    }catch(e){
      Alert.alert('Download unavailable',e instanceof Error?e.message:'Unable to download this purchase.');
    }finally{setDownloading(null)}
  },[]);

  return <SafeAreaView style={s.safe}><View style={s.container}><Text style={s.title}>Library</Text>{busy?<ActivityIndicator/>:error?<Text style={s.error}>{error}</Text>:items.length===0?<Text style={s.empty}>Your purchased beats will appear here.</Text>:<FlatList data={items} keyExtractor={x=>x.id} renderItem={({item})=><View style={s.item}><View style={{flex:1}}><Text style={s.name}>{item.track_title||'BeatHub purchase'}</Text><Text style={s.meta}>{item.order_number} · {item.currency} {item.amount.toFixed(0)}</Text></View><View style={s.actions}><Text style={item.status==='completed'?s.done:s.pending}>{item.status}</Text>{item.status==='completed'&&<Pressable disabled={downloading===item.id} onPress={()=>download(item)} style={s.download}><Text style={s.downloadText}>{downloading===item.id?'Preparing…':'Download'}</Text></Pressable>}</View></View>}/>}</View></SafeAreaView>
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{flex:1,padding:20},title:{fontSize:30,fontWeight:'800',color:'#fff',marginTop:15,marginBottom:22},item:{flexDirection:'row',alignItems:'center',paddingVertical:17,borderBottomWidth:1,borderBottomColor:'#211d28'},name:{color:'#fff',fontSize:16,fontWeight:'700'},meta:{color:'#817b8b',fontSize:12,marginTop:5},actions:{alignItems:'flex-end',gap:8},done:{color:'#b8f5c7',fontWeight:'700'},pending:{color:'#f1c97b',fontWeight:'700'},download:{paddingHorizontal:12,paddingVertical:8,borderRadius:8,backgroundColor:'#fff'},downloadText:{color:'#0d0b12',fontSize:12,fontWeight:'800'},empty:{color:'#8d8798',marginTop:20},error:{color:'#ff8f8f'}});
