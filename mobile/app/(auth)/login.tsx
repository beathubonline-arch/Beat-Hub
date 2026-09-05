import { useState } from 'react';
import { Link, router } from 'expo-router';
import { ActivityIndicator, Pressable, SafeAreaView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useAuth } from '../../src/auth/AuthContext';

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit() {
    setError(''); setBusy(true);
    try { await login(email.trim(), password); router.replace('/(tabs)/home'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to sign in.'); }
    finally { setBusy(false); }
  }

  return <SafeAreaView style={styles.safe}><View style={styles.container}>
    <Text style={styles.logo}>BeatHub</Text>
    <Text style={styles.heading}>Welcome back</Text>
    <Text style={styles.sub}>Sign in to your existing BeatHub account.</Text>
    <TextInput style={styles.input} placeholder="Email" placeholderTextColor="#8d8798" autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} />
    <TextInput style={styles.input} placeholder="Password" placeholderTextColor="#8d8798" secureTextEntry value={password} onChangeText={setPassword} />
    {!!error && <Text style={styles.error}>{error}</Text>}
    <Pressable style={styles.button} onPress={submit} disabled={busy}>{busy ? <ActivityIndicator /> : <Text style={styles.buttonText}>Sign in</Text>}</Pressable>
    <Link href="/(auth)/signup" style={styles.link}>Create an account</Link>
  </View></SafeAreaView>;
}

const styles = StyleSheet.create({ safe:{flex:1,backgroundColor:'#0d0b12'}, container:{flex:1,justifyContent:'center',padding:28}, logo:{fontSize:34,fontWeight:'800',color:'#fff',marginBottom:44}, heading:{fontSize:28,fontWeight:'700',color:'#fff'},sub:{color:'#aaa3b4',marginTop:8,marginBottom:28,fontSize:15},input:{backgroundColor:'#181520',borderRadius:12,padding:16,color:'#fff',marginBottom:12},button:{backgroundColor:'#fff',padding:16,borderRadius:12,alignItems:'center',marginTop:8},buttonText:{color:'#0d0b12',fontWeight:'700'},link:{color:'#fff',textAlign:'center',marginTop:22},error:{color:'#ff8f8f',marginBottom:8}
});
