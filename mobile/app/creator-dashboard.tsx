import { useCallback, useState } from "react";
import { router, useFocusEffect } from "expo-router";
import {
  ActivityIndicator,
  Linking,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { api } from "../src/api";
import { useAuth } from "../src/auth/AuthContext";
import { palette, radius } from "../src/theme";
import {
  BrandLockup,
  PrimaryButton,
  ScreenHeading,
  SectionTitle,
} from "../src/ui";

type CurrencyStats = {
  sales: number;
  gross: number;
  commission: number;
  net: number;
  available: number;
  pending_withdrawal: number;
};
type Dashboard = {
  profile: { stage_name?: string; slug?: string; store_url?: string };
  stats: {
    total_sales: number;
    gross_revenue: number;
    platform_commission: number;
    net_earnings: number;
    available_balance: number;
    pending_withdrawal: number;
  };
  tracks: {
    id: string;
    title: string;
    price: number;
    currency: string;
    is_sold: boolean;
  }[];
};
type Finance = { currencies: Record<string, CurrencyStats> };

export default function CreatorDashboard() {
  const { user } = useAuth();
  const [data, setData] = useState<Dashboard | null>(null);
  const [finance, setFinance] = useState<Finance | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      const [dashboard, summary] = await Promise.all([
        api<Dashboard>("/creator/dashboard"),
        api<Finance>("/creator/financial-summary"),
      ]);
      setData(dashboard);
      setFinance(summary);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Unable to load creator dashboard.",
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );
  if (loading)
    return (
      <SafeAreaView style={s.safe}>
        <View style={s.center}>
          <ActivityIndicator />
          <Text style={s.muted}>Loading dashboard…</Text>
        </View>
      </SafeAreaView>
    );
  if (error)
    return (
      <SafeAreaView style={s.safe}>
        <View style={s.container}>
          <Text
            style={{ color: palette.white, fontSize: 30, fontWeight: "900" }}
          >
            Creator dashboard
          </Text>
          <Text style={s.error}>{error}</Text>
          <Pressable style={s.button} onPress={load}>
            <Text style={s.buttonText}>Try again</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  if (!data) return null;
  const currency = (code: string) => (code === "USD" ? "$" : "KSh");
  const stats = (code: string): CurrencyStats =>
    finance?.currencies?.[code] || {
      sales: 0,
      gross: 0,
      commission: 0,
      net: 0,
      available: 0,
      pending_withdrawal: 0,
    };
  const money = (code: string, v: number) =>
    `${currency(code)} ${v.toFixed(2)}`;
  const kes = stats("KES");
  const usd = stats("USD");
  return (
    <SafeAreaView style={s.safe}>
      <ScrollView
        contentContainerStyle={s.container}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              load();
            }}
            tintColor={palette.gold}
          />
        }
      >
        <BrandLockup compact />
        <View style={s.heading}>
          <ScreenHeading
            kicker="CREATOR STUDIO"
            title={data.profile.stage_name || user?.stage_name || "Creator"}
            copy="Your releases, audience and earnings in one serious workspace."
          />
        </View>
        <View style={s.hero}>
          <Text style={s.heroKicker}>AVAILABLE TO WITHDRAW</Text>
          <Text style={s.heroAmount}>{money("KES", kes.available)}</Text>
          <Text style={s.heroCopy}>
            {kes.sales} completed sales · {money("KES", kes.pending_withdrawal)}{" "}
            pending
          </Text>
          <View style={s.heroLine}>
            <View style={s.heroLineGold} />
            <View style={s.heroLineTeal} />
          </View>
        </View>
        <PrimaryButton
          label="UPLOAD NEW RELEASE"
          icon="＋"
          onPress={() => router.push("/creator-upload")}
        />
        <View style={s.actions}>
          <Pressable
            style={s.action}
            onPress={() => router.push("/creator-profile")}
          >
            <Text style={s.actionText}>Edit profile</Text>
          </Pressable>
          <Pressable
            style={s.action}
            onPress={() => router.push("/creator-finances")}
          >
            <Text style={s.actionText}>Sales & withdrawals</Text>
          </Pressable>
        </View>
        {!!data.profile.store_url && (
          <Pressable
            style={s.secondary}
            onPress={() => Linking.openURL(data.profile.store_url!)}
          >
            <Text style={s.secondaryText}>Open public store ↗</Text>
          </Pressable>
        )}
        <SectionTitle kicker="YOUR MONEY" title="Earnings by currency" />
        <View style={s.earnCard}>
          <Text style={s.cardLabel}>KES</Text>
          <Text style={s.big}>{money("KES", kes.available)}</Text>
          <Text style={s.cardMeta}>
            Available to withdraw · {kes.sales} sales
          </Text>
          <Text style={s.small}>
            Net earned: {money("KES", kes.net)} · Pending:{" "}
            {money("KES", kes.pending_withdrawal)}
          </Text>
        </View>
        <View style={s.earnCard}>
          <Text style={s.cardLabel}>USD</Text>
          <Text style={s.big}>{money("USD", usd.available)}</Text>
          <Text style={s.cardMeta}>
            {usd.sales} sales · USD balance is kept separate
          </Text>
          <Text style={s.small}>Net earned: {money("USD", usd.net)}</Text>
        </View>
        <View style={s.grid}>
          <View style={s.stat}>
            <Text style={s.label}>Total sales</Text>
            <Text style={s.value}>{data.stats.total_sales}</Text>
          </View>
          <View style={s.stat}>
            <Text style={s.label}>Tracks</Text>
            <Text style={s.value}>{data.tracks.length}</Text>
          </View>
        </View>
        <Pressable
          style={s.secondary}
          onPress={() => router.push("/notifications")}
        >
          <Text style={s.secondaryText}>Notifications</Text>
        </Pressable>
        <Pressable
          style={s.secondary}
          onPress={() => router.push("/(tabs)/beats")}
        >
          <Text style={s.secondaryText}>View marketplace</Text>
        </Pressable>
        <SectionTitle kicker="CATALOGUE" title="Your releases" />
        {data.tracks.length === 0 ? (
          <Text style={s.muted}>No tracks uploaded yet.</Text>
        ) : (
          data.tracks.slice(0, 12).map((t, index) => (
            <View key={t.id} style={s.track}>
              <View style={s.trackIndex}>
                <Text style={s.trackIndexText}>
                  {String(index + 1).padStart(2, "0")}
                </Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={s.trackTitle}>{t.title}</Text>
                <Text style={s.trackMeta}>
                  {t.currency} {t.price.toFixed(2)}
                  {t.is_sold ? " · SOLD" : ""}
                </Text>
              </View>
              <Text style={s.trackArrow}>›</Text>
            </View>
          ))
        )}
      </ScrollView>
    </SafeAreaView>
  );
}
const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.canvas },
  container: { padding: 18, paddingTop: 16, paddingBottom: 42 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  heading: { marginTop: 24, marginBottom: 18 },
  muted: { color: palette.muted, fontSize: 14, lineHeight: 21, marginTop: 6 },
  error: { color: palette.danger, marginTop: 20 },
  hero: {
    overflow: "hidden",
    backgroundColor: "#17121D",
    borderRadius: radius.lg,
    padding: 21,
    borderWidth: 1,
    borderColor: palette.borderGold,
    marginBottom: 12,
  },
  heroKicker: {
    color: palette.gold,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 1.7,
  },
  heroAmount: {
    color: palette.white,
    fontSize: 36,
    fontWeight: "900",
    letterSpacing: -1.5,
    marginTop: 6,
  },
  heroCopy: { color: palette.muted, fontSize: 12, marginTop: 6 },
  heroLine: {
    flexDirection: "row",
    height: 4,
    borderRadius: 4,
    overflow: "hidden",
    marginTop: 20,
  },
  heroLineGold: { flex: 3, backgroundColor: palette.gold },
  heroLineTeal: { flex: 1, backgroundColor: palette.teal },
  earnCard: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    padding: 18,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: palette.border,
  },
  cardLabel: {
    color: palette.teal,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 1.4,
  },
  big: { color: palette.white, fontSize: 25, fontWeight: "900", marginTop: 8 },
  cardMeta: { color: palette.text, fontSize: 12, marginTop: 6 },
  small: { color: palette.muted2, fontSize: 11, marginTop: 8 },
  grid: { flexDirection: "row", gap: 10, marginTop: 8 },
  stat: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    padding: 16,
    flex: 1,
    minHeight: 85,
    borderWidth: 1,
    borderColor: palette.border,
  },
  label: { color: palette.muted, fontSize: 11 },
  value: {
    color: palette.white,
    fontSize: 22,
    fontWeight: "900",
    marginTop: 8,
  },
  actions: { flexDirection: "row", gap: 10, marginTop: 12 },
  action: {
    flex: 1,
    backgroundColor: palette.surface,
    borderRadius: radius.sm,
    padding: 15,
    alignItems: "center",
    borderWidth: 1,
    borderColor: palette.border,
  },
  actionText: { color: palette.white, fontWeight: "800", fontSize: 12 },
  secondary: {
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.sm,
    padding: 14,
    alignItems: "center",
    marginTop: 10,
  },
  secondaryText: { color: palette.text, fontWeight: "800", fontSize: 12 },
  button: {
    backgroundColor: palette.gold,
    padding: 15,
    borderRadius: radius.sm,
    alignItems: "center",
    marginTop: 18,
  },
  buttonText: { color: palette.ink, fontWeight: "900" },
  track: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    padding: 13,
    marginBottom: 9,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    borderColor: palette.border,
  },
  trackIndex: {
    width: 38,
    height: 38,
    borderRadius: 11,
    backgroundColor: palette.surfaceRaised,
    alignItems: "center",
    justifyContent: "center",
  },
  trackIndexText: { color: palette.gold, fontWeight: "900", fontSize: 10 },
  trackTitle: { color: palette.white, fontWeight: "900", fontSize: 15 },
  trackMeta: { color: palette.muted2, marginTop: 4, fontSize: 11 },
  trackArrow: { color: palette.muted2, fontSize: 25 },
});
