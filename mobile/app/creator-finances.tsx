import { useCallback, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import { ActivityIndicator, RefreshControl, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { api } from '../src/api';

type Sale={id:string;order_number:string;track_title:string;gross_amount:number;commission_amount:number;net_amount:number;currency:string;completed_at:string|null};
type Withdrawal={id:string;amount:number;currency:string;phone_number:string;status:string;admin_note:string|null;created_at:string|null;resolved_at:string|null};
type Data={sales:Sale[];withdrawals:Withdrawal[]};

export default function CreatorFinances(){
 const [data,setData]=useState<Data>({sales:[],withdrawals:[]}); const [loading,setLoading]=useState(true); const [refreshing,setRefreshing]=useState(false); const [error,setError]=useState('');
 const load=useCallback(async()=>{setError('');try{const [sales,withdrawals]=await Promise.all([api<{items:Sale[]}>('/creator/sales'),api<{items:Withdrawal[]}>('/creator/withdrawals')]);setData({sales:sales.items,withdrawals:withdrawals.items});}catch(e){setError(e instanceof Error?e.message:'Unable to load creator finances.');}finally{setLoading(false);setRefreshing(false);}},[]);
 useFocusEffect(useCallback(()=>{load();},[load]));
 if(loading)return <SafeAreaView style={s.safe}><View style={s.center}><ActivityIndicator/><Text style={s.muted}>Loading finances…</Text></View></SafeAreaView>;
 return <SafeAreaView style={s.safe}><ScrollView contentContainerStyle={s.container} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={()=>{setRefreshing(true);load();}} />}>
  <Text style={s.eyebrow}>CREATOR FINANCES</Text><Text style={s.title}>Sales & withdrawals</Text>{error?<Text style={s.error}>{error}</Text>:null}
  <Text style={s.section}>Recent sales</Text>{data.sales.length===0?<Text style={s.muted}>No completed sales yet.</Text>:data.sales.map(x=><View key={x.id} style={s.card}><Text style={s.track}>{x.track_title||'Track'}</Text><Text style={s.meta}>{x.order_number} · {x.currency} {x.gross_amount.toFixed(2)}</Text><Text style={s.net}>Creator earnings: {x.currency} {x.net_amount.toFixed(2)}</Text></View>)}
  <Text style={s.section}>Withdrawal history</Text>{data.withdrawals.length===0?<Text style={s.muted}>No withdrawal requests yet.</Text>:data.withdrawals.map(x=><View key={x.id} style={s.card}><Text style={s.track}>{x.currency} {x.amount.toFixed(2)}</Text><Text style={s.meta}>{x.status.toUpperCase()} · {x.phone_number}</Text>{x.admin_note?<Text style={s.note}>{x.admin_note}</Text>:null}</View>)}
 </ScrollView></SafeAreaView>;
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{padding:22,paddingBottom:50},center:{flex:1,alignItems:'center',justifyContent:'center'},eyebrow:{color:'#918b9b',fontSize:12,fontWeight:'800',letterSpacing:1.5,marginTop:14},title:{color:'#fff',fontSize:30,fontWeight:'800',marginTop:7},section:{color:'#fff',fontSize:21,fontWeight:'800',marginTop:28,marginBottom:12},muted:{color:'#918b9b',fontSize:15,lineHeight:21,marginTop:6},error:{color:'#ff8f8f',marginTop:18},card:{backgroundColor:'#181520',borderRadius:15,padding:16,marginBottom:10},track:{color:'#fff',fontSize:16,fontWeight:'800'},meta:{color:'#918b9b',marginTop:6},net:{color:'#fff',fontWeight:'700',marginTop:10},note:{color:'#c8c1d0',marginTop:8}});
