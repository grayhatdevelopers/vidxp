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
  return <SpectrumButtonElement tag="sp-button" {...props} />;
}

export function SpectrumActionButton(props: SpectrumActionButtonProps) {
  return <SpectrumButtonElement tag="sp-action-button" {...props} />;
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

export function SpectrumTextField({
  ariaLabel,
  className,
  disabled = false,
  label,
  onValueChange,
  placeholder,
  quiet = false,
  type = "text",
  value,
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

export function SpectrumTextArea({
  ariaLabel,
  className,
  disabled = false,
  label,
  onValueChange,
  placeholder,
  quiet = false,
  value,
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

export function SpectrumCheckbox({
  ariaLabel,
  checked,
  children,
  className,
  disabled = false,
  indeterminate = false,
  onCheckedChange,
  onPress,
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
