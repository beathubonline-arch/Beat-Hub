import { useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { ActivityIndicator, Pressable, SafeAreaView, StyleSheet, Text, TextInput, View } from 'react-native';
import { api } from '../../src/api';

export default function VerifyEmail() {
  const params = useLocalSearchParams<{ email?: string }>();
  const [email, setEmail] = useState(params.email || '');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function verify() {
    setBusy(true); setError(''); setMessage('');
    try {
      await api('/auth/verify-email', { method: 'POST', body: JSON.stringify({ email: email.trim(), code: code.replace(/\s/g, '') }) });
      setMessage('Email verified. You can now sign in.');
      setTimeout(() => router.replace('/(auth)/login'), 700);
    } catch (e) { setError(e instanceof Error ? e.message : 'Verification failed.'); }
    finally { setBusy(false); }
  }

  async function resend() {
    setResending(true); setError(''); setMessage('');
    try {
      const r = await api<{ message: string }>('/auth/resend-verification', { method: 'POST', body: JSON.stringify({ email: email.trim(), password: 'verification-request' }) });
      setMessage(r.message);
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to resend the code.'); }
    finally { setResending(false); }
  }

  return <SafeAreaView style={s.safe}><View style={s.container}>
    <Text style={s.logo}>BeatHub</Text>
    <Text style={s.heading}>Verify your email</Text>
    <Text style={s.sub}>Enter the 6-digit code sent to your email address.</Text>
    <TextInput style={s.input} placeholder="Email" placeholderTextColor="#8d8798" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail}/>
    <TextInput style={s.input} placeholder="6-digit code" placeholderTextColor="#8d8798" keyboardType="number-pad" maxLength={6} value={code} onChangeText={setCode}/>
    {!!error && <Text style={s.error}>{error}</Text>}
    {!!message && <Text style={s.message}>{message}</Text>}
    <Pressable style={s.button} onPress={verify} disabled={busy || code.length !== 6 || !email.trim()}>{busy ? <ActivityIndicator/> : <Text style={s.buttonText}>Verify email</Text>}</Pressable>
    <Pressable style={s.resend} onPress={resend} disabled={resending || !email.trim()}><Text style={s.resendText}>{resending ? 'Sending…' : 'Resend code'}</Text></Pressable>
    <Pressable onPress={() => router.replace('/(auth)/login')}><Text style={s.link}>Back to sign in</Text></Pressable>
  </View></SafeAreaView>;
}

const s = StyleSheet.create({ safe:{flex:1,backgroundColor:'#0d0b12'}, container:{flex:1,justifyContent:'center',padding:28}, logo:{fontSize:34,fontWeight:'800',color:'#fff',marginBottom:30}, heading:{fontSize:27,fontWeight:'700',color:'#fff'}, sub:{color:'#aaa3b4',marginTop:8,marginBottom:22,lineHeight:21}, input:{backgroundColor:'#181520',borderRadius:12,padding:16,color:'#fff',marginBottom:11}, button:{backgroundColor:'#fff',padding:16,borderRadius:12,alignItems:'center',marginTop:12}, buttonText:{color:'#0d0b12',fontWeight:'700'}, resend:{padding:16,alignItems:'center',marginTop:4}, resendText:{color:'#fff',fontWeight:'700'}, link:{color:'#aaa3b4',textAlign:'center',marginTop:8}, error:{color:'#ff8f8f',marginTop:8}, message:{color:'#b8f5c7',marginTop:8}
});
