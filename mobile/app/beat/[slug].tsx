import { useEffect, useState } from "react";
import { router, useLocalSearchParams } from "expo-router";
import { useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import {
  ActivityIndicator,
  Image,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { api, Track } from "../../src/api";
import { palette, radius, shadow } from "../../src/theme";
import { BrandLockup } from "../../src/ui";

function currencyLabel(currency?: string | null) {
  const value = String(currency || "KES")
    .trim()
    .toUpperCase();
  return value === "USD" ? "$" : value === "KES" ? "KSh" : value;
}

function timeLabel(seconds: number) {
  const safe = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
}

function PreviewPlayer({ url }: { url: string }) {
  const player = useAudioPlayer(url, {
    updateInterval: 500,
    downloadFirst: false,
  });
  const status = useAudioPlayerStatus(player);

  const toggle = async () => {
    if (status.playing) {
      player.pause();
      return;
    }
    if (status.duration > 0 && status.currentTime >= status.duration - 0.2)
      await player.seekTo(0);
    player.play();
  };

  const progress =
    status.duration > 0 ? Math.min(1, status.currentTime / status.duration) : 0;
  return (
    <View style={s.playerCard}>
      <Pressable
        style={s.preview}
        onPress={toggle}
        disabled={!status.isLoaded && status.isBuffering}
      >
        {status.isBuffering ? (
          <ActivityIndicator />
        ) : (
          <Text style={s.previewText}>
            {status.playing ? "❚❚ Pause preview" : "▶ Play preview"}
          </Text>
        )}
      </Pressable>
      <View style={s.progressTrack}>
        <View style={[s.progressFill, { width: `${progress * 100}%` }]} />
      </View>
      <View style={s.timeRow}>
        <Text style={s.time}>{timeLabel(status.currentTime)}</Text>
        <Text style={s.time}>{timeLabel(status.duration)}</Text>
      </View>
      {!!status.error && (
        <Text style={s.error}>Preview could not be played on this device.</Text>
      )}
    </View>
  );
}

export default function BeatDetails() {
  const { slug } = useLocalSearchParams<{ slug: string }>();
  const [track, setTrack] = useState<Track | null>(null);
  const [busy, setBusy] = useState(true);
  const [buying, setBuying] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!slug) return;
    let active = true;
    (async () => {
      try {
        const next = await api<Track>(`/catalog/${encodeURIComponent(slug)}`);
        if (active) setTrack(next);
      } catch (e) {
        if (active)
          setError(e instanceof Error ? e.message : "Unable to load track.");
      } finally {
        if (active) setBusy(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [slug]);

  async function buy() {
    if (!track || track.is_sold) return;
    setBuying(true);
    setError("");
    try {
      const r = await api<{
        order_id: string;
        reference: string;
        authorization_url: string;
      }>("/payments/paystack/initialize", {
        method: "POST",
        body: JSON.stringify({ slug: track.slug }),
      });
      router.push(
        `/checkout/${r.order_id}?url=${encodeURIComponent(r.authorization_url)}&reference=${encodeURIComponent(r.reference)}`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to start checkout.");
    } finally {
      setBuying(false);
    }
  }

  if (busy)
    return (
      <SafeAreaView style={s.safe}>
        <View style={s.center}>
          <ActivityIndicator />
        </View>
      </SafeAreaView>
    );
  if (!track)
    return (
      <SafeAreaView style={s.safe}>
        <View style={s.container}>
          <Text style={s.error}>{error || "Track not found."}</Text>
        </View>
      </SafeAreaView>
    );

  const symbol = currencyLabel(track.currency);
  const price = Number(track.price || 0).toFixed(2);
  return (
    <SafeAreaView style={s.safe}>
      <ScrollView contentContainerStyle={s.container}>
        <View style={s.topRow}>
          <Pressable onPress={() => router.back()} style={s.backButton}>
            <Text style={s.back}>‹</Text>
          </Pressable>
          <BrandLockup compact />
        </View>
        <View style={s.art}>
          {track.artwork_url ? (
            <Image
              source={{ uri: track.artwork_url }}
              style={s.artImage}
              resizeMode="cover"
            />
          ) : (
            <Text style={s.note}>♪</Text>
          )}
          <View style={s.artShade} />
          <View style={s.artBadge}>
            <Text style={s.artBadgeText}>BEATHUB ORIGINAL</Text>
          </View>
        </View>
        <Text style={s.kicker}>LICENCE THIS SOUND</Text>
        <Text style={s.title}>{track.title}</Text>
        <View style={s.producerRow}>
          <Text style={s.producer}>@{track.producer || "BeatHub Creator"}</Text>
          {track.producer_verified && (
            <View style={s.verified}>
              <Text style={s.verifiedText}>✓ VERIFIED</Text>
            </View>
          )}
        </View>
        <View style={s.meta}>
          <Text style={s.metaText}>{track.genre || "Music"}</Text>
          <Text style={s.metaText}>
            {track.bpm ? `${track.bpm} BPM` : "Beat"}
          </Text>
          <Text style={s.metaText}>
            {track.sales_model === "non_exclusive"
              ? "Non-exclusive"
              : "Exclusive"}
          </Text>
        </View>
        {!!track.description && (
          <Text style={s.description}>{track.description}</Text>
        )}
        {!!track.preview_url && <PreviewPlayer url={track.preview_url} />}
        {!!error && <Text style={s.error}>{error}</Text>}
        <View style={s.licenceCard}>
          <View>
            <Text style={s.licenceLabel}>
              {track.sales_model === "non_exclusive"
                ? "NON-EXCLUSIVE LICENCE"
                : "EXCLUSIVE LICENCE"}
            </Text>
            <Text style={s.price}>
              {symbol} {price}
            </Text>
            <Text style={s.priceNote}>
              {track.is_sold
                ? "This exclusive beat has been sold."
                : "Secure checkout · instant library access"}
            </Text>
          </View>
          <View style={s.lock}>
            <Text style={s.lockText}>♬</Text>
          </View>
        </View>
        <Pressable
          style={[s.buy, track.is_sold && s.disabled]}
          onPress={buy}
          disabled={buying || track.is_sold}
        >
          {buying ? (
            <ActivityIndicator color={palette.ink} />
          ) : (
            <>
              <Text style={s.buyText}>
                {track.is_sold ? "SOLD" : "BUY LICENCE"}
              </Text>
              <Text style={s.buyArrow}>→</Text>
            </>
          )}
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.canvas },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  container: { padding: 18, paddingBottom: 50 },
  topRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginVertical: 12,
  },
  backButton: {
    width: 38,
    height: 38,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: palette.border,
    backgroundColor: palette.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  back: { color: palette.white, fontSize: 27, lineHeight: 29 },
  art: {
    height: 330,
    borderRadius: radius.lg,
    backgroundColor: palette.surfaceRaised,
    overflow: "hidden",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 23,
    borderWidth: 1,
    borderColor: palette.borderGold,
    ...shadow,
  },
  artImage: { width: "100%", height: "100%" },
  artShade: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: 0,
    bottom: 0,
    backgroundColor: "rgba(8,7,12,0.08)",
  },
  artBadge: {
    position: "absolute",
    left: 14,
    top: 14,
    borderRadius: radius.pill,
    backgroundColor: "rgba(8,7,12,0.78)",
    paddingHorizontal: 11,
    paddingVertical: 7,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.14)",
  },
  artBadgeText: {
    color: palette.gold,
    fontSize: 8,
    fontWeight: "900",
    letterSpacing: 1.2,
  },
  note: { fontSize: 80, color: palette.gold },
  kicker: {
    color: palette.teal,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 2,
  },
  title: {
    fontSize: 34,
    lineHeight: 38,
    fontWeight: "900",
    color: palette.white,
    letterSpacing: -1.4,
    marginTop: 6,
  },
  producerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 9,
    marginTop: 8,
  },
  producer: { fontSize: 14, color: palette.muted, fontWeight: "700" },
  verified: {
    backgroundColor: "rgba(24,213,170,0.10)",
    borderRadius: radius.pill,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  verifiedText: {
    color: palette.teal,
    fontWeight: "900",
    fontSize: 7,
    letterSpacing: 0.8,
  },
  meta: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 18 },
  metaText: {
    color: palette.text,
    backgroundColor: palette.surface,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: radius.pill,
    fontSize: 10,
    fontWeight: "800",
    borderWidth: 1,
    borderColor: palette.border,
  },
  description: { color: palette.muted, lineHeight: 22, marginTop: 20 },
  playerCard: {
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    padding: 15,
    marginTop: 22,
    borderWidth: 1,
    borderColor: palette.border,
  },
  preview: {
    borderWidth: 1,
    borderColor: palette.borderGold,
    backgroundColor: "rgba(255,184,0,0.07)",
    padding: 14,
    borderRadius: radius.sm,
    alignItems: "center",
  },
  previewText: {
    color: palette.goldBright,
    fontWeight: "900",
    letterSpacing: 0.4,
  },
  progressTrack: {
    height: 5,
    borderRadius: 5,
    backgroundColor: palette.border,
    overflow: "hidden",
    marginTop: 14,
  },
  progressFill: { height: "100%", backgroundColor: palette.teal },
  timeRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 7,
  },
  time: { color: palette.muted2, fontSize: 10 },
  licenceCard: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: "#17121D",
    borderWidth: 1,
    borderColor: palette.borderGold,
    borderRadius: radius.md,
    padding: 17,
    marginTop: 18,
  },
  licenceLabel: {
    color: palette.gold,
    fontSize: 8,
    fontWeight: "900",
    letterSpacing: 1.2,
  },
  price: {
    color: palette.white,
    fontSize: 27,
    fontWeight: "900",
    marginTop: 4,
  },
  priceNote: { color: palette.muted2, fontSize: 10, marginTop: 4 },
  lock: {
    width: 46,
    height: 46,
    borderRadius: 23,
    backgroundColor: "rgba(255,184,0,0.12)",
    alignItems: "center",
    justifyContent: "center",
  },
  lockText: { color: palette.gold, fontSize: 22 },
  buy: {
    backgroundColor: palette.gold,
    minHeight: 56,
    borderRadius: radius.sm,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 12,
    marginTop: 12,
  },
  disabled: { opacity: 0.45 },
  buyText: { fontWeight: "900", color: palette.ink, letterSpacing: 1 },
  buyArrow: { fontWeight: "900", fontSize: 19, color: palette.ink },
  error: { color: palette.danger, paddingVertical: 12 },
});
