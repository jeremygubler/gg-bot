import type { Embed } from "../api";
import { useTr } from "../i18n";

/** `#rrggbb` für das Farbfeld; Discord rechnet mit der Zahl. */
export function colorToHex(color: number | null): string {
  return `#${(color ?? 0).toString(16).padStart(6, "0")}`;
}

export function hexToColor(hex: string): number {
  return Number.parseInt(hex.replace("#", ""), 16);
}

/**
 * Vereinfachte Darstellung einer Discord-Nachricht. Bilder werden bewusst nicht geladen: die
 * Adresse stammt vom Nutzer, und ein Abruf würde die IP-Adresse des Betrachters an Dritte geben.
 */
export function MessagePreview({ content, embed }: { content: string; embed: Embed | null }) {
  const tr = useTr();
  const hasEmbed = embed !== null;
  return (
    <div aria-label={tr.t("ann.preview")} role="group" className="rounded bg-[#313338] p-3 text-sm text-slate-100">
      {content !== "" && <p className="mb-2 whitespace-pre-wrap break-words">{content}</p>}
      {hasEmbed && (
        <div
          data-testid="embed"
          className="max-w-md rounded border-l-4 bg-[#2b2d31] p-3"
          style={{ borderLeftColor: embed.color === null ? "#1e1f22" : colorToHex(embed.color) }}
        >
          {embed.author && <p className="mb-1 text-xs font-semibold">{embed.author}</p>}
          {embed.title && <p className="mb-1 font-semibold">{embed.title}</p>}
          {embed.description && <p className="whitespace-pre-wrap break-words text-slate-300">{embed.description}</p>}
          {embed.fields.length > 0 && (
            <div className="mt-2 grid grid-cols-3 gap-2">
              {embed.fields.map((field, index) => (
                <div key={index} className={field.inline ? "col-span-1" : "col-span-3"}>
                  <p className="text-xs font-semibold">{field.name}</p>
                  <p className="whitespace-pre-wrap break-words text-slate-300">{field.value}</p>
                </div>
              ))}
            </div>
          )}
          {(embed.image_url || embed.thumbnail_url) && (
            <p className="mt-2 rounded bg-slate-700/60 px-2 py-1 text-xs text-slate-400">
              {[embed.image_url ? tr.t("ann.image") : null, embed.thumbnail_url ? tr.t("ann.thumbnail") : null]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          {embed.footer && <p className="mt-2 text-xs text-slate-400">{embed.footer}</p>}
        </div>
      )}
      <p className="mt-2 text-xs text-slate-500">{tr.t("ann.previewNote")}</p>
    </div>
  );
}
