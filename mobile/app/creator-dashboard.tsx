import { useCallback, useState } from 'react';
import { router, useFocusEffect } from 'expo-router';
import { ActivityIndicator, Pressable, RefreshControl, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { api } from '../src/api';
import { useAuth } from '../src/auth/AuthContext';

type Dashboard={profile:{stage_name?:string;slug?:string;store_url?:string};stats:{total_sales:number;gross_revenue:number;platform_commission:number;net_earnings:number;available_balance:number;pending_withdrawal:number};tracks:{id:string;title:string;price:number;currency:string;is_sold:boolean}[]};
export default function CreatorDashboard(){
 const {user}=useAuth(); const [data,setData]=useState<Dashboard|null>(null); const [loading,setLoading]=useState(true); const [refreshing,setRefreshing]=useState(false); const [error,setError]=useState('');
 const load=useCallback(async()=>{setError('');try{setData(await api<Dashboard>('/creator/dashboard'));}catch(e){setError(e instanceof Error?e.message:'Unable to load creator dashboard.');}finally{setLoading(false);setRefreshing(false);}},[]);
 useFocusEffect(useCallback(()=>{load();},[load]));
 if(loading)return <SafeAreaView style={s.safe}><View style={s.center}><ActivityIndicator/><Text style={s.muted}>Loading dashboard…</Text></View></SafeAreaView>;
 if(error)return <SafeAreaView style={s.safe}><View style={s.container}><Text style={s.title}>Creator dashboard</Text><Text style={s.error}>{error}</Text><Pressable style={s.button} onPress={load}><Text style={s.buttonText}>Try again</Text></Pressable></View></SafeAreaView>;
 if(!data)return null; const money=(v:number)=>`KES ${v.toFixed(2)}`;
 return <SafeAreaView style={s.safe}><ScrollView contentContainerStyle={s.container} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={()=>{setRefreshing(true);load();}} />}>
  <Text style={s.eyebrow}>CREATOR STUDIO</Text><Text style={s.title}>{data.profile.stage_name||user?.stage_name||'Creator'}</Text><Text style={s.muted}>Manage your music, sales and earnings.</Text>
  <View style={s.grid}><View style={s.stat}><Text style={s.label}>Available</Text><Text style={s.value}>{money(data.stats.available_balance)}</Text></View><View style={s.stat}><Text style={s.label}>Net earnings</Text><Text style={s.value}>{money(data.stats.net_earnings)}</Text></View><View style={s.stat}><Text style={s.label}>Sales</Text><Text style={s.value}>{data.stats.total_sales}</Text></View><View style={s.stat}><Text style={s.label}>Tracks</Text><Text style={s.value}>{data.tracks.length}</Text></View></View>
  <Pressable style={s.secondary} onPress={()=>router.push('/(tabs)/beats')}><Text style={s.secondaryText}>View marketplace</Text></Pressable>
  <Text style={s.section}>Your tracks</Text>{data.tracks.length===0?<Text style={s.muted}>No tracks uploaded yet.</Text>:data.tracks.slice(0,12).map(t=><View key={t.id} style={s.track}><View style={{flex:1}}><Text style={s.trackTitle}>{t.title}</Text><Text style={s.trackMeta}>{t.currency} {t.price.toFixed(2)}{t.is_sold?' · Sold':''}</Text></View></View>)}
 </ScrollView></SafeAreaView>;
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{padding:22,paddingBottom:40},center:{flex:1,alignItems:'center',justifyContent:'center'},eyebrow:{color:'#918b9b',fontSize:12,fontWeight:'800',letterSpacing:1.5,marginTop:14},title:{color:'#fff',fontSize:31,fontWeight:'800',marginTop:7},muted:{color:'#918b9b',fontSize:15,lineHeight:21,marginTop:6},error:{color:'#ff8f8f',marginTop:20},grid:{flexDirection:'row',flexWrap:'wrap',gap:10,marginTop:24},stat:{backgroundColor:'#181520',borderRadius:16,padding:16,width:'48%',minHeight:95},label:{color:'#918b9b',fontSize:12},value:{color:'#fff',fontSize:19,fontWeight:'800',marginTop:10},secondary:{borderWidth:1,borderColor:'#302b38',borderRadius:13,padding:15,alignItems:'center',marginTop:18},secondaryText:{color:'#fff',fontWeight:'700'},button:{backgroundColor:'#fff',padding:15,borderRadius:12,alignItems:'center',marginTop:18},buttonText:{color:'#0d0b12',fontWeight:'700'},section:{color:'#fff',fontSize:21,fontWeight:'800',marginTop:28,marginBottom:12},track:{backgroundColor:'#181520',borderRadius:14,padding:16,marginBottom:9,flexDirection:'row'},trackTitle:{color:'#fff',fontWeight:'700',fontSize:16},trackMeta:{color:'#918b9b',marginTop:5}})
