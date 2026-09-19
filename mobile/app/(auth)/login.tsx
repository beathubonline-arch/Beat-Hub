import { useState } from 'react';
import { Link, router } from 'expo-router';
import { Linking, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useAuth } from '../../src/auth/AuthContext';
import { palette, radius } from '../../src/theme';
import { BrandLockup, Field, PrimaryButton } from '../../src/ui';

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit() {
    const cleanEmail = email.trim().toLowerCase();
    if (!cleanEmail || !password) { setError('Enter your email and password.'); return; }
    setError(''); setBusy(true);
    try { await login(cleanEmail, password); router.replace('/(tabs)/home'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to sign in.'); }
    finally { setBusy(false); }
  }
  return <SafeAreaView style={s.safe}><ScrollView contentContainerStyle={s.container} keyboardShouldPersistTaps="handled">
    <BrandLockup />
    <View style={s.hero}><View style={s.glow}/><Text style={s.kicker}>YOUR MUSIC. YOUR BUSINESS.</Text><Text style={s.heading}>Welcome back to the <Text style={s.gold}>sound.</Text></Text><Text style={s.sub}>Sign in to discover beats, manage purchases and run your creator studio.</Text></View>
    <View style={s.form}><Text style={s.label}>EMAIL ADDRESS</Text><Field placeholder="you@example.com" autoCapitalize="none" autoComplete="email" keyboardType="email-address" value={email} onChangeText={setEmail}/><Text style={s.label}>PASSWORD</Text><Field placeholder="Your password" secureTextEntry autoComplete="password" value={password} onChangeText={setPassword} onSubmitEditing={submit} returnKeyType="go"/><Pressable onPress={()=>Linking.openURL('https://mybeathub.com/forgot-password')}><Text style={s.forgot}>Forgot password?</Text></Pressable>{!!error&&<View style={s.errorBox}><Text style={s.error}>{error}</Text></View>}<PrimaryButton label="SIGN IN TO BEATHUB" icon="→" onPress={submit} busy={busy}/></View>
    <Text style={s.member}>NEW TO BEATHUB?</Text><Link href="/(auth)/signup" style={s.link}>Create your free account →</Link><Text style={s.foot}>One account. Web and mobile. Built for the culture.</Text>
  </ScrollView></SafeAreaView>;
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:palette.canvas},container:{flexGrow:1,paddingHorizontal:22,paddingTop:24,paddingBottom:34},hero:{overflow:'hidden',borderWidth:1,borderColor:palette.borderGold,backgroundColor:'#15101D',borderRadius:radius.lg,padding:22,marginTop:28},glow:{position:'absolute',width:190,height:190,borderRadius:95,backgroundColor:'rgba(255,184,0,0.08)',right:-60,top:-75},kicker:{color:palette.teal,fontWeight:'900',fontSize:9,letterSpacing:2},heading:{color:palette.white,fontWeight:'900',fontSize:34,lineHeight:37,letterSpacing:-1.5,marginTop:12},gold:{color:palette.gold},sub:{color:palette.muted,lineHeight:21,marginTop:13},form:{gap:10,marginTop:22},label:{color:palette.muted,fontSize:9,fontWeight:'900',letterSpacing:1.4,marginTop:5},forgot:{color:palette.goldBright,textAlign:'right',fontSize:12,fontWeight:'800',marginBottom:4},errorBox:{borderWidth:1,borderColor:'rgba(255,143,156,0.25)',backgroundColor:'rgba(255,143,156,0.08)',borderRadius:radius.sm,padding:12},error:{color:palette.danger,lineHeight:18,fontSize:12},member:{textAlign:'center',color:palette.muted2,fontWeight:'900',fontSize:9,letterSpacing:1.5,marginTop:24},link:{color:palette.white,textAlign:'center',fontWeight:'900',marginTop:7},foot:{color:palette.muted2,textAlign:'center',fontSize:10,marginTop:28}});
