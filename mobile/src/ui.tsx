import type { ReactNode } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
} from "react-native";
import { palette, radius } from "./theme";

export function BrandLockup({ compact = false }: { compact?: boolean }) {
  return (
    <View style={u.brandRow}>
      <View style={[u.brandMark, compact && u.brandMarkCompact]}>
        <Text style={u.brandMarkText}>B</Text>
      </View>
      <Text style={[u.brand, compact && u.brandCompact]}>
        BEAT<Text style={u.gold}>HUB</Text>
      </Text>
    </View>
  );
}

export function ScreenHeading({
  kicker,
  title,
  copy,
  right,
}: {
  kicker: string;
  title: string;
  copy?: string;
  right?: ReactNode;
}) {
  return (
    <View style={u.headingRow}>
      <View style={u.headingCopy}>
        <Text style={u.kicker}>{kicker}</Text>
        <Text style={u.title}>{title}</Text>
        {!!copy && <Text style={u.copy}>{copy}</Text>}
      </View>
      {right}
    </View>
  );
}

export function Field(props: TextInputProps) {
  return (
    <TextInput
      placeholderTextColor={palette.muted2}
      {...props}
      style={[u.field, props.multiline && u.multiline, props.style]}
    />
  );
}

export function PrimaryButton({
  label,
  onPress,
  busy,
  disabled,
  icon,
}: {
  label: string;
  onPress: () => void;
  busy?: boolean;
  disabled?: boolean;
  icon?: string;
}) {
  return (
    <Pressable
      disabled={busy || disabled}
      onPress={onPress}
      style={({ pressed }) => [
        u.primary,
        (busy || disabled) && u.disabled,
        pressed && u.pressed,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={palette.ink} />
      ) : (
        <>
          <Text style={u.primaryText}>{label}</Text>
          {icon && <Text style={u.primaryIcon}>{icon}</Text>}
        </>
      )}
    </Pressable>
  );
}

export function SectionTitle({
  kicker,
  title,
}: {
  kicker?: string;
  title: string;
}) {
  return (
    <View style={u.section}>
      {kicker && <Text style={u.sectionKicker}>{kicker}</Text>}
      <Text style={u.sectionTitle}>{title}</Text>
    </View>
  );
}

const u = StyleSheet.create({
  brandRow: { flexDirection: "row", alignItems: "center" },
  brandMark: {
    width: 42,
    height: 42,
    borderRadius: 14,
    backgroundColor: palette.gold,
    alignItems: "center",
    justifyContent: "center",
  },
  brandMarkCompact: { width: 34, height: 34, borderRadius: 11 },
  brandMarkText: { color: palette.ink, fontSize: 21, fontWeight: "900" },
  brand: {
    color: palette.white,
    fontWeight: "900",
    fontSize: 22,
    letterSpacing: -0.8,
    marginLeft: 10,
  },
  brandCompact: { fontSize: 18 },
  gold: { color: palette.gold },
  headingRow: { flexDirection: "row", alignItems: "flex-end", gap: 12 },
  headingCopy: { flex: 1 },
  kicker: {
    color: palette.gold,
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 2.1,
  },
  title: {
    color: palette.white,
    fontSize: 33,
    lineHeight: 37,
    fontWeight: "900",
    letterSpacing: -1.2,
    marginTop: 5,
  },
  copy: { color: palette.muted, fontSize: 13, lineHeight: 20, marginTop: 7 },
  field: {
    minHeight: 54,
    backgroundColor: palette.surface,
    borderRadius: radius.sm,
    paddingHorizontal: 16,
    color: palette.white,
    borderWidth: 1,
    borderColor: palette.border,
    fontSize: 15,
  },
  multiline: { minHeight: 112, paddingTop: 15, textAlignVertical: "top" },
  primary: {
    minHeight: 54,
    borderRadius: radius.sm,
    backgroundColor: palette.gold,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
    paddingHorizontal: 18,
  },
  primaryText: {
    color: palette.ink,
    fontWeight: "900",
    fontSize: 13,
    letterSpacing: 0.8,
  },
  primaryIcon: { color: palette.ink, fontWeight: "900", fontSize: 19 },
  disabled: { opacity: 0.45 },
  pressed: { opacity: 0.82, transform: [{ scale: 0.985 }] },
  section: { marginTop: 28, marginBottom: 12 },
  sectionKicker: {
    color: palette.teal,
    fontSize: 8,
    fontWeight: "900",
    letterSpacing: 1.8,
    marginBottom: 4,
  },
  sectionTitle: {
    color: palette.white,
    fontSize: 21,
    fontWeight: "900",
    letterSpacing: -0.6,
  },
});
