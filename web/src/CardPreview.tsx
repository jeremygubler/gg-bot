import { useEffect, useState } from "react";
import type { CardKind, CardStyle } from "./api";
import { useApi, useGuild } from "./context";
import { useTr } from "./i18n";
import type { Json } from "./schema";

const DELAY_MS = 400;

function toStyle(value: Json | undefined): CardStyle | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const { accent, background, template } = value;
  if (typeof accent !== "number" || typeof background !== "number" || typeof template !== "string") return null;
  return { accent, background, template };
}

/** Zeigt die Karte so, wie der Bot sie zeichnet; folgt dem Formular mit kurzer Verzögerung. */
export function CardPreview({ kind, value }: { kind: CardKind; value: Json | undefined }) {
  const api = useApi();
  const tr = useTr();
  const { id } = useGuild();
  const style = toStyle(value);
  const key = style === null ? "" : `${style.accent}/${style.background}/${style.template}`;
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (style === null) return undefined;
    let current = true;
    let created: string | null = null;
    // Pro Änderung nur eine Anfrage: wer am Farbwähler zieht, löst sonst Dutzende aus.
    const timer = setTimeout(() => {
      api.preview(id, kind, style).then(
        (blob) => {
          if (!current) return;
          created = URL.createObjectURL(blob);
          setUrl((old) => {
            if (old !== null) URL.revokeObjectURL(old);
            return created;
          });
          setFailed(false);
        },
        () => current && setFailed(true),
      );
    }, DELAY_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
    // `style` ist aus `key` abgeleitet; sein Objekt ändert sich bei jedem Rendern.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, id, kind, key]);

  useEffect(
    () => () => {
      setUrl((old) => {
        if (old !== null) URL.revokeObjectURL(old);
        return null;
      });
    },
    [],
  );

  if (style === null) return null;
  return (
    <figure className="mt-6 rounded border border-slate-700 bg-slate-800/40 p-3">
      <figcaption className="mb-2 text-sm text-slate-300">
        {tr.t("preview.title")}: {tr.t(kind === "rank" ? "preview.rank" : "preview.welcome")}
      </figcaption>
      {failed ? (
        <p role="alert" className="text-sm text-red-300">
          {tr.t("preview.error")}
        </p>
      ) : url === null ? (
        <p role="status" className="text-sm text-slate-400">
          {tr.t("preview.loading")}
        </p>
      ) : (
        <img src={url} alt={tr.t(kind === "rank" ? "preview.rank" : "preview.welcome")} className="max-w-full rounded" />
      )}
    </figure>
  );
}
