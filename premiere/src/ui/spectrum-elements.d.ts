import type { HTMLAttributes, RefAttributes } from "react";

interface SpectrumElementAttributes
  extends Omit<HTMLAttributes<HTMLElement>, "className">,
    RefAttributes<HTMLElement> {
  checked?: true;
  class?: string;
  disabled?: true;
  indeterminate?: true;
  placeholder?: string;
  quiet?: true;
  selected?: true;
  type?: string;
  value?: number | string;
  variant?: string;
}

declare module "react/jsx-runtime" {
  namespace JSX {
    interface IntrinsicElements {
      "sp-action-button": SpectrumElementAttributes;
      "sp-button": SpectrumElementAttributes;
      "sp-checkbox": SpectrumElementAttributes;
      "sp-label": SpectrumElementAttributes;
      "sp-textarea": SpectrumElementAttributes;
      "sp-textfield": SpectrumElementAttributes;
    }
  }
}
