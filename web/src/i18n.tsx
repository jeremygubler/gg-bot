import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export type Locale = "de" | "en";
type Pair = readonly [de: string, en: string];

const STORAGE_KEY = "ggbot.locale";

/** Oberflächentexte. Neue Schlüssel immer in beiden Sprachen; ein Test erzwingt das Muster. */
const ui = {
  "app.title": ["GG-Bot Dashboard", "GG-Bot Dashboard"],
  "login.title": ["Anmelden", "Sign in"],
  "login.text": [
    "Melde dich mit Discord an, um die Einstellungen deiner Server zu verwalten.",
    "Sign in with Discord to manage your servers' settings.",
  ],
  "login.button": ["Mit Discord anmelden", "Sign in with Discord"],
  "logout": ["Abmelden", "Sign out"],
  "loading": ["Lade …", "Loading …"],
  "retry": ["Erneut versuchen", "Try again"],
  "servers.title": ["Deine Server", "Your servers"],
  "servers.empty": [
    "Du verwaltest keinen Server, auf dem du Rechte hast.",
    "You don't manage any server.",
  ],
  "servers.invite": ["Bot einladen", "Invite the bot"],
  "servers.nobot": ["Bot nicht auf dem Server", "Bot is not on this server"],
  "servers.open": ["Öffnen", "Open"],
  "nav.servers": ["Server", "Servers"],
  "nav.audit": ["Änderungsprotokoll", "Change log"],
  "nav.modules": ["Module", "Modules"],
  "guild.choose": ["Wähle links ein Modul.", "Pick a module on the left."],
  "form.save": ["Speichern", "Save"],
  "form.saving": ["Speichere …", "Saving …"],
  "form.saved": ["Gespeichert.", "Saved."],
  "form.reset": ["Verwerfen", "Discard"],
  "form.unsaved": ["Ungespeicherte Änderungen", "Unsaved changes"],
  "form.confirmLeave": [
    "Es gibt ungespeicherte Änderungen. Wirklich verlassen?",
    "You have unsaved changes. Leave anyway?",
  ],
  "form.invalid": ["Bitte korrigiere die markierten Felder.", "Please fix the marked fields."],
  "form.add": ["Hinzufügen", "Add"],
  "form.remove": ["Entfernen", "Remove"],
  "form.unset": ["nicht gesetzt", "not set"],
  "form.none": ["– keine –", "– none –"],
  "form.choose": ["– wählen –", "– choose –"],
  "form.unknown": ["Unbekannt", "Unknown"],
  "form.limit": ["Höchstens {n} Einträge.", "At most {n} entries."],
  "form.empty": ["Noch keine Einträge.", "No entries yet."],
  "form.enable": ["Festlegen", "Set"],
  "form.key": ["Schlüssel", "Key"],
  "form.level": ["Level", "Level"],
  "form.nothingLeft": ["Alles bereits vergeben.", "Everything is already used."],
  "audit.title": ["Änderungsprotokoll", "Change log"],
  "audit.empty": ["Noch keine Änderungen über das Dashboard.", "No changes made via the dashboard yet."],
  "audit.more": ["Ältere laden", "Load older"],
  "audit.changed": ["{n} Felder geändert", "{n} fields changed"],
  "audit.created": ["Erstmals gespeichert", "Saved for the first time"],
  "audit.by": ["von {user}", "by {user}"],
  "err.unauthenticated": ["Du bist nicht angemeldet.", "You are not signed in."],
  "err.dashboard_disabled": [
    "Das Login ist auf diesem Server nicht eingerichtet.",
    "Login is not configured on this server.",
  ],
  "err.guild_not_found": [
    "Dieser Server ist nicht verfügbar. Fehlen dir Rechte, oder ist der Bot nicht (mehr) dort?",
    "This server is unavailable. Do you lack permissions, or is the bot not there (anymore)?",
  ],
  "err.module_not_found": ["Dieses Modul gibt es nicht.", "This module does not exist."],
  "err.csrf": ["Anfrage abgelehnt. Lade die Seite neu.", "Request rejected. Reload the page."],
  "err.rate_limited": [
    "Zu viele Änderungen in kurzer Zeit. Warte eine Minute.",
    "Too many changes in a short time. Wait a minute.",
  ],
  "err.discord_unavailable": [
    "Discord antwortet gerade nicht. Versuche es gleich noch einmal.",
    "Discord is not responding. Try again shortly.",
  ],
  "err.oauth_state": ["Die Anmeldung ist abgelaufen. Melde dich erneut an.", "Login expired. Sign in again."],
  "err.invalid_settings": ["Die Eingaben sind ungültig.", "The values are invalid."],
  "err.network": ["Keine Verbindung zur API.", "Cannot reach the API."],
  "err.unknown": ["Unerwarteter Fehler.", "Unexpected error."],
  "err.schema": [
    "Das Formular für dieses Modul konnte nicht aufgebaut werden.",
    "The form for this module could not be built.",
  ],
} as const satisfies Record<string, Pair>;

export type UiKey = keyof typeof ui;

const modules: Record<string, Pair> = {
  general: ["Allgemein", "General"],
  moderation: ["Moderation", "Moderation"],
  automod: ["Automod", "Automod"],
  logging: ["Server-Log", "Server log"],
  leveling: ["Leveling", "Leveling"],
  welcome: ["Willkommen & Abschied", "Welcome & goodbye"],
  tickets: ["Tickets", "Tickets"],
  birthday: ["Geburtstage", "Birthdays"],
  starboard: ["Starboard", "Starboard"],
};

/** Feldnamen → Beschriftung. Unbekannte Namen fallen auf eine lesbare Form des Namens zurück. */
const fields: Record<string, Pair> = {
  accent: ["Akzentfarbe", "Accent colour"],
  accountage: ["Konto-Alter", "Account age"],
  action: ["Aktion", "Action"],
  allowed_codes: ["Erlaubte Einladungscodes", "Allowed invite codes"],
  announce: ["Level-Up ankündigen", "Announce level-ups"],
  announce_channel_id: ["Ankündigungs-Kanal", "Announcement channel"],
  announce_message: ["Ankündigungs-Text", "Announcement text"],
  auto_close_hours: ["Automatisch schliessen nach (Stunden, 0 = nie)", "Auto-close after (hours, 0 = never)"],
  autoroles: ["Autorollen für Mitglieder", "Auto-roles for members"],
  background: ["Hintergrundfarbe", "Background colour"],
  bot_autoroles: ["Autorollen für Bots", "Auto-roles for bots"],
  caps: ["Grossbuchstaben", "Caps"],
  card: ["Karte", "Card"],
  channel_id: ["Kanal", "Channel"],
  channel_multipliers: ["XP-Faktor pro Kanal", "XP multiplier per channel"],
  channels: ["Kanal pro Ereignis", "Channel per event"],
  close_delay_seconds: ["Verzögerung vor dem Schliessen (Sekunden)", "Delay before closing (seconds)"],
  cooldown_seconds: ["Abkühlzeit (Sekunden)", "Cooldown (seconds)"],
  count: ["Anzahl", "Count"],
  creators_may_close: ["Ersteller dürfen schliessen", "Creators may close"],
  delete: ["Nachricht löschen", "Delete message"],
  dm_enabled: ["Private Nachricht senden", "Send a direct message"],
  dm_message: ["Text der privaten Nachricht", "Direct message text"],
  dm_on_action: ["Betroffene per DM informieren", "Notify affected users by DM"],
  dm_transcript: ["Protokoll per DM senden", "Send transcript by DM"],
  domains: ["Domains", "Domains"],
  duplicates: ["Wiederholungen", "Duplicates"],
  duration_seconds: ["Dauer (Sekunden)", "Duration (seconds)"],
  embed_color: ["Farbe der Bot-Nachrichten", "Colour of bot messages"],
  emoji: ["Emoji", "Emoji"],
  enabled: ["Aktiv", "Enabled"],
  entries: ["Einträge", "Entries"],
  escalation: ["Eskalation", "Escalation"],
  exempt_channels: ["Ausgenommene Kanäle", "Exempt channels"],
  exempt_moderators: ["Moderatoren ausnehmen", "Exempt moderators"],
  exempt_roles: ["Ausgenommene Rollen", "Exempt roles"],
  feedback: ["Bewertung abfragen", "Ask for feedback"],
  ignore_bots: ["Bots ignorieren", "Ignore bots"],
  ignore_nsfw: ["NSFW-Kanäle ignorieren", "Ignore NSFW channels"],
  ignored_channels: ["Ignorierte Kanäle", "Ignored channels"],
  invites: ["Einladungen", "Invites"],
  join_card: ["Bildkarte beim Beitritt", "Picture card on join"],
  join_channel_id: ["Beitritts-Kanal", "Join channel"],
  join_enabled: ["Beitritt begrüssen", "Greet joins"],
  join_message: ["Beitritts-Text", "Join text"],
  joins: ["Beitritte", "Joins"],
  leave_channel_id: ["Abschieds-Kanal", "Leave channel"],
  leave_enabled: ["Abschied melden", "Announce leaves"],
  leave_message: ["Abschieds-Text", "Leave text"],
  links: ["Links", "Links"],
  log_channel_id: ["Log-Kanal", "Log channel"],
  max_mentions: ["Höchstens Erwähnungen", "Max mentions"],
  max_open_per_user: ["Offene Tickets pro Person", "Open tickets per person"],
  max_percent: ["Höchstanteil in Prozent", "Max percent"],
  mentions: ["Erwähnungen", "Mentions"],
  message: ["Text", "Text"],
  messages: ["Nachrichten", "Messages"],
  min_days: ["Mindestalter in Tagen", "Minimum age in days"],
  min_length: ["Mindestlänge", "Minimum length"],
  mode: ["Modus", "Mode"],
  mode_minutes: ["Dauer des Raid-Modus (Minuten)", "Raid mode duration (minutes)"],
  multiplier: ["XP-Faktor", "XP multiplier"],
  name_template: ["Namensvorlage", "Name template"],
  no_xp_channels: ["Kanäle ohne XP", "Channels without XP"],
  no_xp_roles: ["Rollen ohne XP", "Roles without XP"],
  pattern: ["Muster", "Pattern"],
  ping_support: ["Support-Rolle erwähnen", "Ping the support role"],
  raid: ["Anti-Raid", "Anti-raid"],
  requires_company: ["Nur mit Begleitung", "Requires company"],
  retention_days: ["Aufbewahrung (Tage, 0 = unbegrenzt)", "Retention (days, 0 = unlimited)"],
  reward_mode: ["Belohnungs-Modus", "Reward mode"],
  rewards: ["Rollen-Belohnungen", "Role rewards"],
  role_id: ["Rolle", "Role"],
  role_multipliers: ["XP-Faktor pro Rolle", "XP multiplier per role"],
  seconds: ["Sekunden", "Seconds"],
  self_star: ["Eigene Sterne zählen", "Count own stars"],
  spam: ["Spam", "Spam"],
  template: ["Vorlage", "Template"],
  threshold: ["Schwelle", "Threshold"],
  timeout_seconds: ["Timeout (Sekunden)", "Timeout (seconds)"],
  type: ["Art", "Type"],
  voice: ["Sprachkanal-XP", "Voice XP"],
  warn: ["Verwarnen", "Warn"],
  warn_expiry_days: ["Verwarnungen verfallen nach (Tagen)", "Warnings expire after (days)"],
  warns: ["Verwarnungen", "Warnings"],
  words: ["Wörter", "Words"],
  xp_max: ["XP höchstens", "XP maximum"],
  xp_min: ["XP mindestens", "XP minimum"],
};

const enums: Record<string, Pair> = {
  off: ["Aus", "Off"],
  current: ["Im aktuellen Kanal", "In the current channel"],
  channel: ["In festem Kanal", "In a fixed channel"],
  dm: ["Per DM", "By DM"],
  stack: ["Stapeln", "Stack"],
  replace: ["Ersetzen", "Replace"],
  kick: ["Kick", "Kick"],
  ban: ["Bann", "Ban"],
  tempban: ["Temporärer Bann", "Temporary ban"],
  timeout: ["Timeout", "Timeout"],
  blacklist: ["Sperrliste", "Block list"],
  whitelist: ["Erlaubnisliste", "Allow list"],
  word: ["Wort", "Word"],
  wildcard: ["Platzhalter", "Wildcard"],
  regex: ["Regulärer Ausdruck", "Regular expression"],
  solid: ["Einfarbig", "Solid"],
  gradient: ["Verlauf", "Gradient"],
  stripes: ["Streifen", "Stripes"],
  dots: ["Punkte", "Dots"],
  message_delete: ["Nachricht gelöscht", "Message deleted"],
  message_edit: ["Nachricht bearbeitet", "Message edited"],
  message_bulk: ["Massen-Löschung", "Bulk delete"],
  member_join: ["Beitritt", "Member joined"],
  member_leave: ["Austritt", "Member left"],
  member_update: ["Mitglied geändert", "Member updated"],
  member_ban: ["Bann", "Member banned"],
  member_unban: ["Entbannung", "Member unbanned"],
  role_create: ["Rolle erstellt", "Role created"],
  role_delete: ["Rolle gelöscht", "Role deleted"],
  role_update: ["Rolle geändert", "Role updated"],
  channel_create: ["Kanal erstellt", "Channel created"],
  channel_delete: ["Kanal gelöscht", "Channel deleted"],
  channel_update: ["Kanal geändert", "Channel updated"],
  voice: ["Sprachkanal", "Voice"],
  invite_create: ["Einladung erstellt", "Invite created"],
  invite_delete: ["Einladung gelöscht", "Invite deleted"],
};

export function humanize(name: string): string {
  const spaced = name.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function pick(pair: Pair | undefined, locale: Locale): string | undefined {
  return pair === undefined ? undefined : pair[locale === "de" ? 0 : 1];
}

export type Translator = {
  locale: Locale;
  t: (key: UiKey, params?: Record<string, string | number>) => string;
  module: (name: string) => string;
  field: (name: string) => string;
  enumValue: (value: string) => string;
  error: (code: string) => string;
};

export function makeTranslator(locale: Locale): Translator {
  const t: Translator["t"] = (key, params = {}) => {
    let text = pick(ui[key], locale) ?? key;
    for (const [name, value] of Object.entries(params)) text = text.replace(`{${name}}`, String(value));
    return text;
  };
  return {
    locale,
    t,
    module: (name) => pick(modules[name], locale) ?? humanize(name),
    field: (name) => pick(fields[name], locale) ?? humanize(name),
    enumValue: (value) => pick(enums[value], locale) ?? humanize(value),
    error: (code) => pick(ui[`err.${code}` as UiKey], locale) ?? t("err.unknown"),
  };
}

/** Für Tests: dieselben Tabellen, ohne React. */
export const tables = { ui, modules, fields, enums };

function initialLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "de" || stored === "en") return stored;
  } catch {
    // Speicher gesperrt (privates Fenster): Browsersprache nehmen.
  }
  return navigator.language.toLowerCase().startsWith("de") ? "de" : "en";
}

const Context = createContext<{ tr: Translator; setLocale: (locale: Locale) => void } | null>(null);

export function LocaleProvider({ children, initial }: { children: ReactNode; initial?: Locale }) {
  const [locale, setLocaleState] = useState<Locale>(initial ?? initialLocale());
  const value = useMemo(
    () => ({
      tr: makeTranslator(locale),
      setLocale: (next: Locale) => {
        setLocaleState(next);
        try {
          localStorage.setItem(STORAGE_KEY, next);
        } catch {
          // Nur eine Komfortfunktion.
        }
      },
    }),
    [locale],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useTr(): Translator {
  const value = useContext(Context);
  if (value === null) throw new Error("LocaleProvider fehlt");
  return value.tr;
}

export function useSetLocale(): (locale: Locale) => void {
  const value = useContext(Context);
  if (value === null) throw new Error("LocaleProvider fehlt");
  return value.setLocale;
}
