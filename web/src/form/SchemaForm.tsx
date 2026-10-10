import { createContext, useContext, useId, useState, type ReactNode } from "react";
import type { Snapshot } from "../api";
import { useTr, type Translator } from "../i18n";
import {
  blank,
  pathKey,
  type Field,
  type Json,
  type JsonObject,
  type KeySpec,
  type PickerKind,
  type Spec,
} from "../schema";
import { labelOf, options, type Option } from "./pickers";

type Path = readonly (string | number)[];

type FormContext = { snapshot: Snapshot; errors: Map<string, string[]> };
const Context = createContext<FormContext | null>(null);

function useForm(): FormContext {
  const value = useContext(Context);
  if (value === null) throw new Error("SchemaForm fehlt");
  return value;
}

type Change = (next: Json) => void;

export function SchemaForm(props: {
  spec: Extract<Spec, { kind: "object" }>;
  value: JsonObject;
  onChange: (next: JsonObject) => void;
  snapshot: Snapshot;
  errors: Map<string, string[]>;
}) {
  return (
    <Context.Provider value={{ snapshot: props.snapshot, errors: props.errors }}>
      <div className="space-y-4">
        <Fields
          fields={props.spec.fields}
          value={props.value}
          path={[]}
          onChange={(next) => props.onChange(next)}
        />
      </div>
    </Context.Provider>
  );
}

function Fields(props: { fields: Field[]; value: JsonObject; path: Path; onChange: (next: JsonObject) => void }) {
  return (
    <>
      {props.fields.map((field) => (
        <FieldView
          key={field.name}
          field={field}
          value={props.value[field.name] ?? null}
          path={[...props.path, field.name]}
          onChange={(next) => props.onChange({ ...props.value, [field.name]: next })}
        />
      ))}
    </>
  );
}

function Errors({ path }: { path: Path }) {
  const { errors } = useForm();
  const messages = errors.get(pathKey(path));
  if (messages === undefined) return null;
  return (
    <ul role="alert" className="mt-1 text-sm text-red-400">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
}

const INPUT =
  "w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-slate-100 focus:border-indigo-400 focus:outline-none";

export function FieldView(props: {
  field: Field;
  value: Json;
  path: Path;
  onChange: Change;
  /** Eigene Beschriftung (Listeneinträge: „Eskalation 2“); `hideLabel` lässt sie nur für Screenreader. */
  label?: string;
  hideLabel?: boolean;
}) {
  const tr = useTr();
  const { errors } = useForm();
  const { field, value, path } = props;
  const label = props.label ?? tr.field(field.name);
  const id = useId();
  const invalid = errors.has(pathKey(path));
  const prefix = `${pathKey(path)}.`;
  const invalidInside = [...errors.keys()].some((key) => key === pathKey(path) || key.startsWith(prefix));

  // Verschachtelte Objekte: einklappbar; hat es einen "enabled"-Schalter, steht er in der Kopfzeile.
  if (field.spec.kind === "object") {
    const object = (value ?? {}) as JsonObject;
    const enabled = field.spec.fields.some((child) => child.name === "enabled") ? object["enabled"] : undefined;
    return (
      <details className="rounded border border-slate-700 bg-slate-800/40" open={invalidInside || undefined}>
        <summary className="cursor-pointer select-none px-3 py-2 font-medium">
          {label}
          {typeof enabled === "boolean" && (
            <span className={`ml-2 text-xs ${enabled ? "text-emerald-400" : "text-slate-500"}`}>
              {enabled ? "●" : "○"}
            </span>
          )}
        </summary>
        <div className="space-y-4 border-t border-slate-700 p-3">
          <Errors path={path} />
          <Fields fields={field.spec.fields} value={object} path={path} onChange={props.onChange} />
        </div>
      </details>
    );
  }

  const container = (children: ReactNode, inline = false) => (
    <div className={inline ? "flex items-center gap-2" : undefined}>
      <label htmlFor={id} className={props.hideLabel ? "sr-only" : "mb-1 block text-sm text-slate-300"}>
        {label}
      </label>
      {children}
      <Errors path={path} />
    </div>
  );

  if (field.spec.kind === "boolean") {
    return (
      <div>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={value === true}
            onChange={(event) => props.onChange(event.target.checked)}
            aria-invalid={invalid}
          />
          <span className={props.hideLabel ? "sr-only" : undefined}>{label}</span>
        </label>
        <Errors path={path} />
      </div>
    );
  }

  if (field.spec.kind === "list" || field.spec.kind === "map") {
    return (
      <fieldset className="rounded border border-slate-700 p-3">
        <legend className="px-1 text-sm text-slate-300">{label}</legend>
        {field.spec.kind === "list" ? (
          <ListEditor label={label} spec={field.spec} value={(value ?? []) as Json[]} path={path} onChange={props.onChange} />
        ) : (
          <MapEditor label={label} spec={field.spec} value={(value ?? {}) as JsonObject} path={path} onChange={props.onChange} />
        )}
        <Errors path={path} />
      </fieldset>
    );
  }

  // Wert oder null: Pickers und Text haben „leer“ als natürliche Entsprechung, der Rest einen Schalter.
  const emptyMeansNull = field.spec.kind === "picker" || field.spec.kind === "text";
  if (field.nullable && !emptyMeansNull) {
    const isNull = value === null;
    return container(
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1 text-sm text-slate-400">
          <input
            type="checkbox"
            checked={!isNull}
            onChange={(event) => props.onChange(event.target.checked ? blank({ ...field, nullable: false }) : null)}
          />
          {tr.t("form.enable")}
        </label>
        {isNull ? (
          <span className="text-sm text-slate-500">{tr.t("form.unset")}</span>
        ) : (
          <Control id={id} spec={field.spec} value={value} invalid={invalid} onChange={props.onChange} />
        )}
      </div>,
    );
  }

  return container(
    <Control
      id={id}
      spec={field.spec}
      value={value}
      invalid={invalid}
      nullable={field.nullable}
      onChange={props.onChange}
    />,
  );
}

function Control(props: {
  id: string;
  spec: Spec;
  value: Json;
  invalid: boolean;
  nullable?: boolean;
  onChange: Change;
}) {
  const tr = useTr();
  const { spec, value, id } = props;
  const aria = { "aria-invalid": props.invalid };
  switch (spec.kind) {
    case "integer":
    case "number":
      return (
        <NumberInput
          id={id}
          value={typeof value === "number" ? value : 0}
          integer={spec.kind === "integer"}
          min={spec.min}
          max={spec.max}
          invalid={props.invalid}
          onChange={props.onChange}
        />
      );
    case "color":
      return (
        <input
          id={id}
          type="color"
          value={intToHex(typeof value === "number" ? value : 0)}
          onChange={(event) => props.onChange(hexToInt(event.target.value))}
          className="h-9 w-16 cursor-pointer rounded border border-slate-600 bg-slate-800"
          {...aria}
        />
      );
    case "text": {
      const text = typeof value === "string" ? value : "";
      const change = (next: string) => props.onChange(props.nullable && next === "" ? null : next);
      return spec.multiline ? (
        <textarea
          id={id}
          rows={4}
          maxLength={spec.maxLength}
          value={text}
          onChange={(event) => change(event.target.value)}
          className={INPUT}
          {...aria}
        />
      ) : (
        <input
          id={id}
          type="text"
          maxLength={spec.maxLength}
          value={text}
          onChange={(event) => change(event.target.value)}
          className={INPUT}
          {...aria}
        />
      );
    }
    case "enum":
      return (
        <select
          id={id}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => props.onChange(event.target.value)}
          className={INPUT}
          {...aria}
        >
          {spec.values.map((option) => (
            <option key={option} value={option}>
              {tr.enumValue(option)}
            </option>
          ))}
        </select>
      );
    case "picker":
      return (
        <PickerSelect
          id={id}
          kind={spec.picker}
          value={typeof value === "string" ? value : null}
          allowEmpty={props.nullable === true}
          invalid={props.invalid}
          onChange={props.onChange}
        />
      );
    default:
      return null;
  }
}

function PickerSelect(props: {
  id?: string;
  kind: PickerKind;
  value: string | null;
  allowEmpty: boolean;
  invalid?: boolean;
  exclude?: string[];
  ariaLabel?: string;
  onChange: (next: string | null) => void;
}) {
  const tr = useTr();
  const { snapshot } = useForm();
  const all = options(snapshot, props.kind);
  const list = all.filter((option) => !(props.exclude ?? []).includes(option.id));
  // Eine gespeicherte ID, die es nicht mehr gibt (gelöschter Kanal), bleibt sichtbar und wählbar,
  // statt beim Speichern still zu verschwinden.
  const missing = props.value !== null && props.value !== "" && labelOf(all, props.value) === undefined;
  return (
    <select
      id={props.id}
      value={props.value ?? ""}
      onChange={(event) => props.onChange(event.target.value === "" ? null : event.target.value)}
      className={INPUT}
      aria-invalid={props.invalid}
      aria-label={props.ariaLabel}
    >
      <option value="">{props.allowEmpty ? tr.t("form.none") : tr.t("form.choose")}</option>
      {missing && (
        <option value={props.value ?? ""}>
          {tr.t("form.unknown")} ({props.value})
        </option>
      )}
      {list.map((option) => (
        <option key={option.id} value={option.id}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function NumberInput(props: {
  id: string;
  value: number;
  integer: boolean;
  min?: number;
  max?: number;
  invalid: boolean;
  onChange: Change;
}) {
  // Eigener Text-Zustand, damit Zwischenstände wie "-" oder "1." beim Tippen nicht zurückspringen.
  const [text, setText] = useState(String(props.value));
  const [shown, setShown] = useState(props.value);
  if (props.value !== shown) {
    setShown(props.value);
    setText(String(props.value));
  }
  const parsed = text.trim() === "" ? NaN : Number(text);
  const bad = Number.isNaN(parsed) || (props.integer && !Number.isInteger(parsed));
  return (
    <input
      id={props.id}
      type="number"
      inputMode={props.integer ? "numeric" : "decimal"}
      min={props.min}
      max={props.max}
      step={props.integer ? 1 : "any"}
      value={text}
      onChange={(event) => {
        setText(event.target.value);
        const next = event.target.value.trim() === "" ? NaN : Number(event.target.value);
        if (!Number.isNaN(next) && (!props.integer || Number.isInteger(next))) {
          setShown(next);
          props.onChange(next);
        }
      }}
      className={`${INPUT} max-w-40`}
      aria-invalid={props.invalid || bad}
    />
  );
}

export function intToHex(value: number): string {
  return `#${Math.max(0, Math.min(0xffffff, Math.trunc(value))).toString(16).padStart(6, "0")}`;
}

export function hexToInt(hex: string): number {
  return parseInt(hex.replace("#", ""), 16);
}

function RemoveButton({ onClick, tr }: { onClick: () => void; tr: Translator }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded px-2 py-1 text-sm text-slate-400 hover:bg-slate-700 hover:text-red-300"
      aria-label={tr.t("form.remove")}
    >
      ✕
    </button>
  );
}

function ListEditor(props: {
  label: string;
  spec: Extract<Spec, { kind: "list" }>;
  value: Json[];
  path: Path;
  onChange: Change;
}) {
  const tr = useTr();
  const { item, maxItems } = props.spec;
  const full = maxItems !== undefined && props.value.length >= maxItems;
  const replace = (index: number, next: Json) =>
    props.onChange(props.value.map((entry, i) => (i === index ? next : entry)));
  const remove = (index: number) => props.onChange(props.value.filter((_, i) => i !== index));

  if (item.spec.kind === "picker") {
    const used = props.value.filter((entry): entry is string => typeof entry === "string");
    return (
      <div>
        <ul className="mb-2 flex flex-wrap gap-2">
          {used.map((id, index) => (
            <PickedChip key={id} kind={item.spec.kind === "picker" ? item.spec.picker : "channel"} id={id} onRemove={() => remove(index)} />
          ))}
        </ul>
        {!full && (
          <PickerSelect
            kind={item.spec.picker}
            ariaLabel={props.label}
            value={null}
            allowEmpty={false}
            exclude={used}
            onChange={(id) => id !== null && props.onChange([...props.value, id])}
          />
        )}
        {full && <p className="text-sm text-slate-500">{tr.t("form.limit", { n: maxItems ?? 0 })}</p>}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {props.value.length === 0 && <p className="text-sm text-slate-500">{tr.t("form.empty")}</p>}
      {props.value.map((entry, index) => (
        <div key={index} className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <FieldView
              field={item}
              label={`${props.label} ${index + 1}`}
              hideLabel={item.spec.kind !== "object"}
              value={entry}
              path={[...props.path, index]}
              onChange={(next) => replace(index, next)}
            />
          </div>
          <RemoveButton onClick={() => remove(index)} tr={tr} />
        </div>
      ))}
      {full ? (
        <p className="text-sm text-slate-500">{tr.t("form.limit", { n: maxItems ?? 0 })}</p>
      ) : (
        <button
          type="button"
          onClick={() => props.onChange([...props.value, blank(item)])}
          className="rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
        >
          + {tr.t("form.add")}
        </button>
      )}
    </div>
  );
}

function PickedChip(props: { kind: PickerKind; id: string; onRemove: () => void }) {
  const tr = useTr();
  const { snapshot } = useForm();
  const label = labelOf(options(snapshot, props.kind), props.id) ?? `${tr.t("form.unknown")} (${props.id})`;
  return (
    <li className="flex items-center gap-1 rounded-full bg-slate-700 py-0.5 pl-3 pr-1 text-sm">
      {label}
      <button
        type="button"
        onClick={props.onRemove}
        className="rounded-full px-1.5 text-slate-400 hover:text-red-300"
        aria-label={`${tr.t("form.remove")}: ${label}`}
      >
        ✕
      </button>
    </li>
  );
}

function MapEditor(props: {
  label: string;
  spec: Extract<Spec, { kind: "map" }>;
  value: JsonObject;
  path: Path;
  onChange: Change;
}) {
  const tr = useTr();
  const { snapshot } = useForm();
  const { key, value: valueField, maxItems } = props.spec;
  const entries = Object.entries(props.value);
  const full = maxItems !== undefined && entries.length >= maxItems;
  const [draft, setDraft] = useState("");

  const keyLabel = (raw: string): string => {
    if (key.kind === "enum") return tr.enumValue(raw);
    if (key.kind === "picker") return labelOf(options(snapshot, key.picker), raw) ?? `${tr.t("form.unknown")} (${raw})`;
    if (key.kind === "integer") return `${tr.t("form.level")} ${raw}`;
    return raw;
  };
  const free = (): Option[] => {
    if (key.kind === "enum") {
      return key.values.filter((v) => !(v in props.value)).map((v) => ({ id: v, label: tr.enumValue(v) }));
    }
    if (key.kind === "picker") return options(snapshot, key.picker).filter((o) => !(o.id in props.value));
    return [];
  };
  const add = (raw: string) => {
    const name = raw.trim();
    if (name === "" || name in props.value) return;
    props.onChange({ ...props.value, [name]: blank(valueField) });
    setDraft("");
  };

  return (
    <div className="space-y-2">
      {entries.length === 0 && <p className="text-sm text-slate-500">{tr.t("form.empty")}</p>}
      {entries.map(([name, entry]) => (
        <div key={name} className="flex items-start gap-2">
          <span className="w-44 shrink-0 pt-1.5 text-sm" aria-hidden="true">
            {keyLabel(name)}
          </span>
          <div className="min-w-0 flex-1">
            <FieldView
              field={valueField}
              label={keyLabel(name)}
              hideLabel
              value={entry}
              path={[...props.path, name]}
              onChange={(next) => props.onChange({ ...props.value, [name]: next })}
            />
          </div>
          <RemoveButton
            onClick={() => {
              const { [name]: _removed, ...rest } = props.value;
              props.onChange(rest);
            }}
            tr={tr}
          />
        </div>
      ))}
      {!full && <KeyAdder label={props.label} keySpec={key} choices={free()} draft={draft} setDraft={setDraft} add={add} />}
      {full && <p className="text-sm text-slate-500">{tr.t("form.limit", { n: maxItems ?? 0 })}</p>}
    </div>
  );
}

function KeyAdder(props: {
  label: string;
  keySpec: KeySpec;
  choices: Option[];
  draft: string;
  setDraft: (value: string) => void;
  add: (raw: string) => void;
}) {
  const tr = useTr();
  if (props.keySpec.kind === "enum" || props.keySpec.kind === "picker") {
    if (props.choices.length === 0) return <p className="text-sm text-slate-500">{tr.t("form.nothingLeft")}</p>;
    return (
      <select
        value=""
        onChange={(event) => props.add(event.target.value)}
        className={INPUT}
        aria-label={`${props.label}: ${tr.t("form.add")}`}
      >
        <option value="">+ {tr.t("form.add")} …</option>
        {props.choices.map((choice) => (
          <option key={choice.id} value={choice.id}>
            {choice.label}
          </option>
        ))}
      </select>
    );
  }
  const label = props.keySpec.kind === "integer" ? tr.t("form.level") : tr.t("form.key");
  return (
    <div className="flex items-center gap-2">
      <input
        type={props.keySpec.kind === "integer" ? "number" : "text"}
        value={props.draft}
        onChange={(event) => props.setDraft(event.target.value)}
        className={`${INPUT} max-w-40`}
        aria-label={`${props.label}: ${label}`}
        placeholder={label}
      />
      <button
        type="button"
        onClick={() => props.add(props.draft)}
        className="rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
      >
        + {tr.t("form.add")}
      </button>
    </div>
  );
}
