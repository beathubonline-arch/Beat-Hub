import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { ActivityIndicator, Pressable, RefreshControl, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { api } from '../src/api';

type Item={id:string;type:string;title:string;message:string;is_read:boolean;created_at:string|null};
export default function Notifications(){
 const [items,setItems]=useState<Item[]>([]); const [loading,setLoading]=useState(true); const [refreshing,setRefreshing]=useState(false); const [error,setError]=useState('');
 const load=useCallback(async()=>{setError('');try{const r=await api<{items:Item[]}>('/notifications');setItems(r.items);}catch(e){setError(e instanceof Error?e.message:'Unable to load notifications.');}finally{setLoading(false);setRefreshing(false);}},[]);
 useFocusEffect(useCallback(()=>{load();},[load]));
 const markRead=async(id:string)=>{try{await api(`/notifications/${id}/read`,{method:'POST'});setItems(v=>v.map(x=>x.id===id?{...x,is_read:true}:x));}catch{} };
 if(loading)return <SafeAreaView style={s.safe}><View style={s.center}><ActivityIndicator/><Text style={s.muted}>Loading notifications…</Text></View></SafeAreaView>;
 return <SafeAreaView style={s.safe}><ScrollView contentContainerStyle={s.container} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={()=>{setRefreshing(true);load();}} />}><Text style={s.eyebrow}>BEATHUB</Text><Text style={s.title}>Notifications</Text>{error?<Text style={s.error}>{error}</Text>:null}{items.length===0?<Text style={s.muted}>You're all caught up.</Text>:items.map(x=><Pressable key={x.id} onPress={()=>!x.is_read&&markRead(x.id)} style={[s.card,!x.is_read&&s.unread]}><View style={s.row}><Text style={s.itemTitle}>{x.title}</Text>{!x.is_read?<View style={s.dot}/>:null}</View><Text style={s.message}>{x.message}</Text><Text style={s.meta}>{x.created_at?new Date(x.created_at).toLocaleString():''}</Text></Pressable>)}</ScrollView></SafeAreaView>;
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{padding:22,paddingBottom:50},center:{flex:1,alignItems:'center',justifyContent:'center'},eyebrow:{color:'#918b9b',fontSize:12,fontWeight:'800',letterSpacing:1.5,marginTop:14},title:{color:'#fff',fontSize:30,fontWeight:'800',marginTop:7,marginBottom:20},card:{backgroundColor:'#181520',borderRadius:15,padding:16,marginBottom:10,borderWidth:1,borderColor:'#181520'},unread:{borderColor:'#3b3545'},row:{flexDirection:'row',alignItems:'center'},itemTitle:{color:'#fff',fontSize:16,fontWeight:'800',flex:1},dot:{width:8,height:8,borderRadius:4,backgroundColor:'#fff',marginLeft:10},message:{color:'#c8c1d0',lineHeight:21,marginTop:8},meta:{color:'#77717f',fontSize:12,marginTop:10},muted:{color:'#918b9b',fontSize:15,lineHeight:21},error:{color:'#ff8f8f',marginBottom:15}});
