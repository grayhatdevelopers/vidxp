import {
  useEffect,
  useRef,
  type ReactNode,
  type RefObject,
} from "react";

type ButtonVariant =
  | "cta"
  | "primary"
  | "secondary"
  | "warning"
  | "overBackground";

interface CommonControlProps {
  ariaLabel?: string;
  className?: string;
  disabled?: boolean;
}

interface SpectrumButtonProps extends CommonControlProps {
  children: ReactNode;
  onPress?: (event: Event) => void;
  quiet?: boolean;
  variant?: ButtonVariant;
}

interface SpectrumActionButtonProps extends CommonControlProps {
  children: ReactNode;
  onPress?: (event: Event) => void;
  quiet?: boolean;
  selected?: boolean;
}

interface SpectrumTextFieldProps extends CommonControlProps {
  label?: string;
  onValueChange: (value: string) => void;
  placeholder?: string;
  quiet?: boolean;
  type?: "text" | "number" | "search";
  value: string;
}

interface SpectrumTextAreaProps extends CommonControlProps {
  label?: string;
  onValueChange: (value: string) => void;
  placeholder?: string;
  quiet?: boolean;
  value: string;
}

interface SpectrumCheckboxProps extends CommonControlProps {
  checked: boolean;
  children?: ReactNode;
  indeterminate?: boolean;
  onCheckedChange: (checked: boolean) => void;
  onPress?: (event: Event) => void;
}

export function SpectrumButton(props: SpectrumButtonProps) {
  if (isCepRuntime()) return <NativeButton {...props} />;
  return <SpectrumButtonElement tag="sp-button" {...props} />;
}

export function SpectrumActionButton(props: SpectrumActionButtonProps) {
  if (isCepRuntime()) return <NativeButton {...props} />;
  return <SpectrumButtonElement tag="sp-action-button" {...props} />;
}

function NativeButton({
  ariaLabel,
  children,
  className,
  disabled = false,
  onPress,
  quiet = false,
  selected = false,
}: SpectrumActionButtonProps) {
  return (
    <button
      aria-label={ariaLabel}
      aria-pressed={selected || undefined}
      className={["native-button", quiet ? "quiet" : "", className]
        .filter(Boolean)
        .join(" ")}
      disabled={disabled}
      onClick={(event) => onPress?.(event.nativeEvent)}
      type="button"
    >
      {children}
    </button>
  );
}

function SpectrumButtonElement({
  tag,
  ariaLabel,
  children,
  className,
  disabled = false,
  onPress,
  quiet = false,
  selected = false,
  variant,
}: SpectrumButtonProps & {
  selected?: boolean;
  tag: "sp-action-button" | "sp-button";
}) {
  const ref = useRef<HTMLElement>(null);
  useUxpProperty(ref, "disabled", disabled);
  useUxpProperty(ref, "quiet", quiet);
  useUxpProperty(ref, "selected", selected);
  useUxpEvent(ref, "click", onPress);

  return tag === "sp-button" ? (
    <sp-button
      ref={ref}
      class={className}
      disabled={booleanAttribute(disabled)}
      quiet={booleanAttribute(quiet)}
      variant={variant}
      aria-label={ariaLabel}
    >
      {children}
    </sp-button>
  ) : (
    <sp-action-button
      ref={ref}
      class={className}
      disabled={booleanAttribute(disabled)}
      quiet={booleanAttribute(quiet)}
      selected={booleanAttribute(selected)}
      aria-label={ariaLabel}
    >
      {children}
    </sp-action-button>
  );
}

export function SpectrumTextField(props: SpectrumTextFieldProps) {
  return isCepRuntime() ? <NativeTextField {...props} /> : <UxpTextField {...props} />;
}

function NativeTextField({
  ariaLabel,
  className,
  disabled = false,
  label,
  onValueChange,
  placeholder,
  type = "text",
  value,
}: SpectrumTextFieldProps) {
  return (
    <label className="native-control">
      {label ? <span>{label}</span> : undefined}
      <input aria-label={ariaLabel} className={className} disabled={disabled}
        onChange={(event) => onValueChange(event.currentTarget.value)}
        placeholder={placeholder} type={type} value={value} />
    </label>
  );
}

function UxpTextField({
  ariaLabel, className, disabled = false, label, onValueChange, placeholder,
  quiet = false, type = "text", value,
}: SpectrumTextFieldProps) {
  const ref = useRef<HTMLElement>(null);
  useUxpProperty(ref, "disabled", disabled);
  useUxpProperty(ref, "quiet", quiet);
  useUxpProperty(ref, "value", value);
  useUxpEvent(ref, "input", (event) => {
    onValueChange(readEventProperty(event, "value", ""));
  });

  return (
    <sp-textfield
      ref={ref}
      class={className}
      disabled={booleanAttribute(disabled)}
      quiet={booleanAttribute(quiet)}
      type={type}
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
    >
      {label ? <sp-label slot="label">{label}</sp-label> : undefined}
    </sp-textfield>
  );
}

export function SpectrumTextArea(props: SpectrumTextAreaProps) {
  return isCepRuntime() ? <NativeTextArea {...props} /> : <UxpTextArea {...props} />;
}

function NativeTextArea({
  ariaLabel,
  className,
  disabled = false,
  label,
  onValueChange,
  placeholder,
  quiet = false,
  value,
}: SpectrumTextAreaProps) {
  void quiet;
  return (
    <label className="native-control">
      {label ? <span>{label}</span> : undefined}
      <textarea aria-label={ariaLabel} className={className} disabled={disabled}
        onChange={(event) => onValueChange(event.currentTarget.value)}
        placeholder={placeholder} value={value} />
    </label>
  );
}

function UxpTextArea({
  ariaLabel, className, disabled = false, label, onValueChange, placeholder,
  quiet = false, value,
}: SpectrumTextAreaProps) {
  const ref = useRef<HTMLElement>(null);
  useUxpProperty(ref, "disabled", disabled);
  useUxpProperty(ref, "quiet", quiet);
  useUxpProperty(ref, "value", value);
  useUxpEvent(ref, "input", (event) => {
    onValueChange(readEventProperty(event, "value", ""));
  });

  return (
    <sp-textarea
      ref={ref}
      class={className}
      disabled={booleanAttribute(disabled)}
      quiet={booleanAttribute(quiet)}
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
    >
      {label ? <sp-label slot="label">{label}</sp-label> : undefined}
    </sp-textarea>
  );
}

export function SpectrumCheckbox(props: SpectrumCheckboxProps) {
  return isCepRuntime() ? <NativeCheckbox {...props} /> : <UxpCheckbox {...props} />;
}

function NativeCheckbox({
  ariaLabel,
  checked,
  children,
  className,
  disabled = false,
  indeterminate = false,
  onCheckedChange,
  onPress,
}: SpectrumCheckboxProps) {
  return (
    <label className={["native-checkbox", className].filter(Boolean).join(" ")}>
      <input aria-label={ariaLabel} checked={checked} disabled={disabled}
        onChange={(event) => onCheckedChange(event.currentTarget.checked)}
        onClick={(event) => onPress?.(event.nativeEvent)}
        ref={(element) => { if (element) element.indeterminate = indeterminate; }}
        type="checkbox" />
      {children ? <span>{children}</span> : undefined}
    </label>
  );
}

function UxpCheckbox({
  ariaLabel, checked, children, className, disabled = false,
  indeterminate = false, onCheckedChange, onPress,
}: SpectrumCheckboxProps) {
  const ref = useRef<HTMLElement>(null);
  useUxpProperty(ref, "checked", checked);
  useUxpProperty(ref, "disabled", disabled);
  useUxpProperty(ref, "indeterminate", indeterminate);
  useUxpEvent(ref, "change", (event) => {
    onCheckedChange(readEventProperty(event, "checked", false));
  });
  useUxpEvent(ref, "click", onPress);

  return (
    <sp-checkbox
      ref={ref}
      class={className}
      checked={booleanAttribute(checked)}
      disabled={booleanAttribute(disabled)}
      indeterminate={booleanAttribute(indeterminate)}
      aria-label={ariaLabel}
    >
      {children}
    </sp-checkbox>
  );
}

function isCepRuntime(): boolean {
  return Reflect.get(window, "__VIDXP_CEP__") === true;
}

function booleanAttribute(value: boolean): true | undefined {
  return value ? true : undefined;
}

function readEventProperty<T>(event: Event, property: string, fallback: T): T {
  const target = event.currentTarget;
  if (!target) return fallback;
  const value = Reflect.get(target, property) as T | undefined;
  return value ?? fallback;
}

function useUxpEvent(
  ref: RefObject<HTMLElement | null>,
  eventName: "change" | "click" | "input",
  handler?: (event: Event) => void,
) {
  useEffect(() => {
    const element = ref.current;
    if (!element || !handler) return;
    element.addEventListener(eventName, handler);
    return () => element.removeEventListener(eventName, handler);
  }, [eventName, handler, ref]);
}

function useUxpProperty<T>(
  ref: RefObject<HTMLElement | null>,
  property: string,
  value: T,
) {
  useEffect(() => {
    const element = ref.current;
    if (element) Reflect.set(element, property, value);
  }, [property, ref, value]);
}
