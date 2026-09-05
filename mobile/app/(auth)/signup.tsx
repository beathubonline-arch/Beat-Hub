import { useState } from 'react';
import { Link, router } from 'expo-router';
import { ActivityIndicator, Pressable, SafeAreaView, StyleSheet, Text, TextInput, View } from 'react-native';
import { api } from '../../src/api';

export default function Signup() {
  const [stageName, setStageName] = useState(''); const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const [role, setRole] = useState<'buyer'|'creator'|'artist'>('buyer'); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit() { setError(''); setBusy(true); try { await api('/auth/signup',{method:'POST',body:JSON.stringify({stage_name:stageName.trim(),email:email.trim(),password,role})}); router.replace('/(auth)/login'); } catch(e){setError(e instanceof Error?e.message:'Unable to create account.')} finally{setBusy(false)} }
  return <SafeAreaView style={s.safe}><View style={s.container}><Text style={s.logo}>BeatHub</Text><Text style={s.heading}>Create your account</Text><Text style={s.sub}>One account for web and mobile.</Text>
    <TextInput style={s.input} placeholder="Stage / display name" placeholderTextColor="#8d8798" value={stageName} onChangeText={setStageName}/>
    <TextInput style={s.input} placeholder="Email" placeholderTextColor="#8d8798" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail}/>
    <TextInput style={s.input} placeholder="Password (8+ characters)" placeholderTextColor="#8d8798" secureTextEntry value={password} onChangeText={setPassword}/>
    <View style={s.roles}>{(['buyer','creator','artist'] as const).map(r=><Pressable key={r} onPress={()=>setRole(r)} style={[s.role,role===r&&s.roleActive]}><Text style={role===r?s.roleTextActive:s.roleText}>{r}</Text></Pressable>)}</View>
    {!!error&&<Text style={s.error}>{error}</Text>}<Pressable style={s.button} onPress={submit} disabled={busy}>{busy?<ActivityIndicator/>:<Text style={s.buttonText}>Create account</Text>}</Pressable><Link href="/(auth)/login" style={s.link}>Already have an account? Sign in</Link>
  </View></SafeAreaView>;
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{flex:1,justifyContent:'center',padding:28},logo:{fontSize:34,fontWeight:'800',color:'#fff',marginBottom:30},heading:{fontSize:27,fontWeight:'700',color:'#fff'},sub:{color:'#aaa3b4',marginTop:8,marginBottom:22},input:{backgroundColor:'#181520',borderRadius:12,padding:16,color:'#fff',marginBottom:11},roles:{flexDirection:'row',gap:8,marginVertical:5},role:{flex:1,padding:12,borderRadius:10,backgroundColor:'#181520',alignItems:'center'},roleActive:{backgroundColor:'#fff'},roleText:{color:'#aaa3b4'},roleTextActive:{color:'#0d0b12',fontWeight:'700'},button:{backgroundColor:'#fff',padding:16,borderRadius:12,alignItems:'center',marginTop:12},buttonText:{color:'#0d0b12',fontWeight:'700'},link:{color:'#fff',textAlign:'center',marginTop:20},error:{color:'#ff8f8f',marginTop:8}});
