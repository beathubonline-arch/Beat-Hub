import { useEffect, useRef, useState } from 'react';
import { Linking, Pressable, SafeAreaView, StyleSheet, Text, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { api } from '../../src/api';

export default function Checkout() {
  const { orderId, url, reference } = useLocalSearchParams<{ orderId: string; url?: string; reference?: string }>();
  const [status, setStatus] = useState('pending');
  const [busy, setBusy] = useState(false);
  const verifying = useRef(false);

  async function verifyPayment() {
    if (!reference || verifying.current) return;
    verifying.current = true;
    try {
      const result = await api<{ status: string; completed: boolean; failed?: boolean }>(
        `/payments/paystack/verify/${encodeURIComponent(reference)}`,
        { method: 'POST' },
      );
      setStatus(result.status);
    } catch {
      // Paystack may still be processing. The status poll below remains authoritative.
    } finally {
      verifying.current = false;
    }
  }

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        await verifyPayment();
        const r = await api<{ status: string }>(`/orders/${orderId}/status`);
        if (active) setStatus(r.status);
      } catch {
        // Keep the current UI state while the provider is processing.
      }
    };
    poll();
    const timer = setInterval(poll, 4000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [orderId, reference]);

  async function open() {
    if (!url) return;
    setBusy(true);
    try {
      await Linking.openURL(url);
    } finally {
      setBusy(false);
    }
  }

  const completed = status === 'completed';
  const failed = status === 'failed' || status === 'rejected';

  return (
    <SafeAreaView style={s.safe}>
      <View style={s.container}>
        <Text style={s.title}>Complete purchase</Text>
        <Text style={s.sub}>Order {orderId}</Text>
        <View style={s.box}>
          <Text style={s.status}>
            {completed ? 'Payment confirmed ✓' : failed ? 'Payment failed' : 'Payment awaiting confirmation'}
          </Text>
          <Text style={s.help}>
            {completed
              ? 'Your purchase is now in your library. You can download the beat there.'
              : failed
                ? 'The payment was not completed. You can return to the marketplace and try again.'
                : 'Continue through Paystack. BeatHub verifies the payment securely and checks the order automatically.'}
          </Text>
        </View>
        {!completed && !failed && (
          <Pressable style={s.button} onPress={open} disabled={busy}>
            <Text style={s.buttonText}>{busy ? 'Opening…' : 'Open Paystack'}</Text>
          </Pressable>
        )}
        {completed && (
          <Pressable style={s.button} onPress={() => router.replace('/(tabs)/library')}>
            <Text style={s.buttonText}>Go to Library & Download</Text>
          </Pressable>
        )}
        <Pressable style={s.secondary} onPress={() => router.replace('/(tabs)/library')}>
          <Text style={s.secondaryText}>Go to Library</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0d0b12' },
  container: { flex: 1, padding: 24, justifyContent: 'center' },
  title: { color: '#fff', fontSize: 30, fontWeight: '800' },
  sub: { color: '#777180', marginTop: 7 },
  box: { backgroundColor: '#181520', borderRadius: 18, padding: 22, marginVertical: 25 },
  status: { color: '#fff', fontSize: 19, fontWeight: '700' },
  help: { color: '#9992a2', lineHeight: 22, marginTop: 10 },
  button: { backgroundColor: '#fff', padding: 17, borderRadius: 13, alignItems: 'center' },
  buttonText: { color: '#0d0b12', fontWeight: '800' },
  secondary: { padding: 17, alignItems: 'center' },
  secondaryText: { color: '#fff' },
});
